import sqlite3

from spec_integrator.db import DocAuditDB
from spec_integrator.models import JudgeEvaluation, JudgeResult


def test_db_crud():
    db = DocAuditDB(":memory:")
    # Insert Document
    db.insert_document("docs/requires/req.md", "0", "requires", "hash123")
    docs = db.get_all_documents()
    assert len(docs) == 1
    assert docs[0]["file_path"] == "docs/requires/req.md"
    assert docs[0]["tier"] == "0"
    # Insert Section
    db.insert_section(
        "sec:docs/requires/req.md#Intro",
        "docs/requires/req.md",
        "Intro",
        1,
        1,
        10,
        "Body text",
        "shash",
    )
    secs = db.get_all_sections()
    assert len(secs) == 1
    assert secs[0]["heading"] == "Intro"
    # Insert Keyword Reference
    db.insert_keyword_reference(
        "REQ_001",
        "docs/requires/req.md",
        "sec:docs/requires/req.md#Intro",
        "defines",
        1,
    )
    refs = db.get_keyword_references("REQ_001")
    assert len(refs) == 1
    assert refs[0]["relation_type"] == "defines"
    # Insert Link
    db.insert_link("docs/tier1/a.md", 5, "docs/tier2/b.md", "sec", 1)
    invalid = db.get_invalid_links()
    assert len(invalid) == 0
    # Cache
    db.set_cache("hash_key_1", "RULE_01", "target_1", "PASS", "Reason OK")
    cache = db.get_cache("hash_key_1")
    assert cache["status"] == "PASS"
    assert cache["reason"] == "Reason OK"
    db.close()


def test_judge_evaluations_can_be_filtered_by_backend():
    db = DocAuditDB(":memory:")
    jev_result = JudgeResult(
        item_id="doc.md#section",
        item_label="doc.md#section",
        status="WARN",
        summary="Jev test judgment",
        evaluations=[
            JudgeEvaluation(
                check_id="clarity",
                classification="possible_violation",
                confidence=0.72,
                location="doc.md#section",
                severity="WARNING",
            )
        ],
        covered_files=["doc.md"],
    )
    nimble_result = JudgeResult(
        item_id="doc.md#section",
        item_label="doc.md#section",
        status="WARN",
        summary="Nimble test judgment",
        evaluations=[
            JudgeEvaluation(
                check_id="clarity",
                classification="possible_violation",
                confidence=0.81,
                location="doc.md#section",
                severity="WARNING",
            )
        ],
        covered_files=["doc.md"],
    )
    db.save_judge_evaluations("llm-single-review", [jev_result], backend="jev")
    db.save_judge_evaluations("llm-single-review", [nimble_result], backend="nimble")

    rows = db.get_judge_evaluations(backend="nimble")

    assert len(rows) == 1
    assert rows[0]["backend"] == "nimble"
    assert rows[0]["confidence"] == 0.81
    db.close()


def test_term_variance_lookups_are_scoped_to_backend():
    db = DocAuditDB(":memory:")
    db.insert_term_variance_judgment(
        term_a="scheduler",
        term_b="dispatcher",
        file_a="a.md",
        file_b="b.md",
        line_a=1,
        line_b=2,
        is_variance=True,
        confidence=0.8,
        preferred_term="scheduler",
        reason="Jev result",
        backend="jev",
    )
    db.insert_term_variance_judgment(
        term_a="scheduler",
        term_b="dispatcher",
        file_a="a.md",
        file_b="b.md",
        line_a=1,
        line_b=2,
        is_variance=True,
        confidence=0.9,
        preferred_term="dispatcher",
        reason="Nimble result",
        backend="nimble",
    )

    assert db.is_similarity_judged("scheduler", "dispatcher", backend="jev")
    assert db.is_similarity_judged("scheduler", "dispatcher", backend="nimble")
    assert [row["backend"] for row in db.get_high_confidence_variances(backend="nimble")] == [
        "nimble"
    ]
    assert [row["backend"] for row in db.get_high_confidence_variances(backend="jev")] == ["jev"]
    db.close()


def test_legacy_term_variance_rows_migrate_without_losing_backend_history(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(db_path)
    legacy.execute("""
        CREATE TABLE term_variance_judgments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term_a TEXT,
            term_b TEXT,
            file_a TEXT,
            file_b TEXT,
            line_a INTEGER,
            line_b INTEGER,
            is_variance INTEGER,
            confidence REAL,
            preferred_term TEXT,
            reason TEXT,
            judged_at TEXT,
            backend TEXT,
            UNIQUE(term_a, term_b, file_a, file_b)
        )
    """)
    legacy.execute(
        """
        INSERT INTO term_variance_judgments
            (term_a, term_b, file_a, file_b, line_a, line_b, is_variance,
             confidence, preferred_term, reason, judged_at, backend)
        VALUES ('scheduler', 'dispatcher', 'a.md', 'b.md', 1, 2, 1,
                0.8, 'scheduler', 'legacy Jev row', '2026-01-01T00:00:00+00:00', 'jev')
        """
    )
    legacy.commit()
    legacy.close()

    db = DocAuditDB(db_path)
    assert db.is_similarity_judged("scheduler", "dispatcher", backend="jev")
    assert not db.is_similarity_judged("scheduler", "dispatcher", backend="nimble")
    db.insert_term_variance_judgment(
        term_a="scheduler",
        term_b="dispatcher",
        file_a="a.md",
        file_b="b.md",
        line_a=1,
        line_b=2,
        is_variance=False,
        confidence=0.6,
        preferred_term="scheduler",
        reason="Nimble row",
        backend="nimble",
    )
    assert db.is_similarity_judged("scheduler", "dispatcher", backend="jev")
    assert db.is_similarity_judged("scheduler", "dispatcher", backend="nimble")
    assert {row["backend"] for row in db.get_all_term_variance_judgments()} == {
        "jev",
        "nimble",
    }
    db.close()
