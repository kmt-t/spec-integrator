from __future__ import annotations

from unittest.mock import patch

from spec_integrator.config import Config, SemanticTopicConfig
from spec_integrator.db import DocAuditDB
from spec_integrator.terminology.section_indexer import SectionTopicIndexer, cosine_similarity
from spec_integrator.verifier.section_verifier import SectionTopicVerifier


def test_cosine_similarity():
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(vec_a, vec_b) - 1.0) < 1e-5

    vec_c = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(vec_a, vec_c) - 0.0) < 1e-5


def test_section_topic_indexing_and_verification(tmp_path):
    db_path = tmp_path / "test_doc_cache.db"
    db = DocAuditDB(db_path)

    # Insert mock documents & sections
    db.insert_document("docs/doc_a.md", 1, "comp_a", "hash_a")
    db.insert_document("docs/doc_b.md", 1, "comp_b", "hash_b")

    db.insert_section(
        "sec:doc_a#heading1",
        "docs/doc_a.md",
        "Heading 1",
        1,
        10,
        30,
        "This is a detailed specification about memory management and buffer pools in Tier 1.",
        "hash_sec_a",
    )
    db.insert_section(
        "sec:doc_b#heading2",
        "docs/doc_b.md",
        "Heading 2",
        1,
        20,
        45,
        "This describes memory management and buffer pools in Tier 2 with duplicate content.",
        "hash_sec_b",
    )
    db.commit()

    config = Config()
    config.semantic_topic = SemanticTopicConfig(
        enabled=True,
        similarity_threshold=0.80,
        unlinked_warning_threshold=0.85,
        duplicate_warning_threshold=0.95,
        embedding_model="test-model",
    )

    # Mock embeddings API returning similar vectors
    indexer = SectionTopicIndexer(config)
    with patch("spec_integrator.terminology.section_indexer.call_sakura_embeddings") as mock_embed:
        mock_embed.return_value = [
            [0.9, 0.1, 0.0],
            [0.88, 0.12, 0.0],
        ]
        embedded = indexer.index_section_embeddings(db, model="test-model")
        assert embedded == 2

    sim_count = indexer.compute_and_save_section_similarities(
        db, model="test-model", min_similarity=0.80
    )
    assert sim_count == 1

    # Verify that unlinked warning is generated (no shared keyword)
    verifier = SectionTopicVerifier(config)
    issues = verifier.verify(db)
    assert len(issues) >= 1
    assert any(i.rule_code in ("SEM-TOPIC-UNLINKED", "SEM-TOPIC-DUPLICATE") for i in issues)

    # Now add common keyword references to both sections
    db.insert_keyword_reference(
        "META_MemoryPool", "docs/doc_a.md", "sec:doc_a#heading1", "defines", 10
    )
    db.insert_keyword_reference(
        "META_MemoryPool", "docs/doc_b.md", "sec:doc_b#heading2", "refers_to", 20
    )
    db.commit()

    # Now that they share a keyword, UNLINKED issue should disappear (if not duplicate)
    config.semantic_topic.duplicate_warning_threshold = 0.999
    issues_linked = verifier.verify(db)
    assert not any(i.rule_code == "SEM-TOPIC-UNLINKED" for i in issues_linked)

    db.close()
