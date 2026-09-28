from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.graph import KeywordGroup
from spec_integrator.judge import UnifiedReviewEngine
from spec_integrator.parser import ParsedDocument, ParsedSection


def _create_sample_doc(file_path: str = "components/test.md") -> ParsedDocument:
    return ParsedDocument(
        file_path=file_path,
        full_path=None,
        tier=1,
        component="test",
        content=(
            "# Test Component\n## Meaning\n<!-- definition: {TestKW} -->\n"
            "{TestKW} means the sample concept.\n## Intro\n"
            "<!-- traceability: {TestKW} -->\nSome introduction text."
        ),
        content_hash="hash123",
        sections=[
            ParsedSection(
                section_id=f"sec:{file_path}#Meaning",
                file_path=file_path,
                heading="Meaning",
                level=2,
                line_start=2,
                line_end=4,
                body_text="<!-- definition: {TestKW} -->\n{TestKW} means the sample concept.",
                keywords=["TestKW"],
                canonical_definition_keywords=["TestKW"],
            ),
            ParsedSection(
                section_id=f"sec:{file_path}#Intro",
                file_path=file_path,
                heading="Intro",
                level=2,
                line_start=5,
                line_end=7,
                body_text="<!-- traceability: {TestKW} -->\nSome introduction text.",
                keywords=["TestKW"],
                reference_keywords=["TestKW"],
            ),
        ],
        all_keywords=["TestKW"],
    )


def test_unified_reviewer_keyword_link_pairs_mock():
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    reviewer = UnifiedReviewEngine(config)
    doc = _create_sample_doc()
    group = KeywordGroup(
        group_id="keyword_group_TestKW",
        keyword="TestKW",
        file_paths=[doc.file_path],
        section_ids=[s.section_id for s in doc.sections],
        total_sections=2,
        total_docs=1,
    )

    results = reviewer.review_keyword_link_pairs(group, [doc], backend="mock")
    assert len(results) == 1
    assert results[0].status == "PASS"
    assert results[0].item_label.startswith("{TestKW} | DEFINITION ")


def test_unified_reviewer_documents_mock():
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    reviewer = UnifiedReviewEngine(config)
    doc = _create_sample_doc()

    res = reviewer.review_single_document(doc, backend="mock")
    assert res.status == "PASS"
    assert res.item_label == doc.file_path


def test_judge_prompt_includes_redundancy_and_layered_criteria():
    """Verify that the redundancy check configured in spec-integrator.yaml instructs the LLM
    to audit redundant duplication while permitting multi-perspective descriptions."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    rule = next((r for r in config.llm_judge.checks if r.id == "redundancy_and_duplication"), None)
    assert rule is not None
    prompt_text = rule.get_prompt_text(config.config_dir)
    assert "same level of detail" in prompt_text
    assert "another viewpoint" in prompt_text
