import json
import pytest
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.obligation import ObligationVerifier


def _setup(tmp_path, body, tags_in_heading=""):
    docs_dir = tmp_path / "docs"
    target = docs_dir / "components" / "tier1_core" / "os_scheduler.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    cfg = Config()
    cfg.config_dir = tmp_path
    doc = MarkdownParser(cfg).parse_file(target, docs_dir)
    return cfg, doc


def _write_risk_report(tmp_path, assessments, doc_hashes=None):
    out = tmp_path / "reports" / "doc_risk_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"total_evaluated": len(assessments), "assessments": assessments}
    if doc_hashes is not None:
        payload["doc_hashes"] = doc_hashes
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


DOC_BODY = """# Scheduler
## 4.1 アルゴリズム
Round-robin scheduling with interrupt wakeup.
"""


def test_missing_assessment_is_an_error(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    issues, summary = ObligationVerifier(cfg).verify([doc])
    assert any(i.rule_code == "OBLIG-ASSESSMENT-MISSING" for i in issues)


def test_high_risk_section_without_the_demanded_tag_is_an_error(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    _write_risk_report(tmp_path, [{
        "file_path": doc.file_path,
        "heading": "4.1 アルゴリズム",
        "risk_score": 4,
        "complexity_score": 3,
        "formal_needed": True,
        "recommended_verification": "pyModelChecking",
        "suggested_tags": ["{VERIFY_FORMAL}"],
    }], doc_hashes={doc.file_path: doc.content_hash})

    issues, summary = ObligationVerifier(cfg).verify([doc])
    skipped = [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"]
    assert len(skipped) == 1
    assert summary.demanded == 1
    assert summary.discharged == 0
    # The error must point at the offending section, not the top of the file.
    assert skipped[0].line == 2


def test_demanded_verification_that_is_tagged_is_discharged(tmp_path):
    cfg, doc = _setup(tmp_path, """# Scheduler {VERIFY_FORMAL}
## 4.1 アルゴリズム
Round-robin scheduling.
""")
    _write_risk_report(tmp_path, [{
        "file_path": doc.file_path,
        "heading": "4.1 アルゴリズム",
        "risk_score": 5,
        "formal_needed": True,
        "recommended_verification": "pyModelChecking",
        "suggested_tags": ["{VERIFY_FORMAL}"],
    }], doc_hashes={doc.file_path: doc.content_hash})

    issues, summary = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"] == []
    assert summary.demanded == 1
    assert summary.discharged == 1


def test_low_risk_section_creates_no_obligation(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    _write_risk_report(tmp_path, [{
        "file_path": doc.file_path,
        "heading": "4.1 アルゴリズム",
        "risk_score": 1,
        "formal_needed": False,
        "recommended_verification": "Static",
        "suggested_tags": [],
    }], doc_hashes={doc.file_path: doc.content_hash})

    issues, summary = ObligationVerifier(cfg).verify([doc])
    assert summary.demanded == 0
    assert [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"] == []


def test_stale_assessment_is_an_error(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    _write_risk_report(tmp_path, [{
        "file_path": doc.file_path, "heading": "4.1 アルゴリズム",
        "risk_score": 1, "formal_needed": False,
        "recommended_verification": "Static", "suggested_tags": [],
    }], doc_hashes={doc.file_path: "0000deadbeef"})

    issues, summary = ObligationVerifier(cfg).verify([doc])
    assert any(i.rule_code == "OBLIG-ASSESSMENT-STALE" for i in issues)
    assert summary.stale_documents == [doc.file_path]


def test_verify_llm_tag_without_a_judge_report_is_an_error(tmp_path):
    cfg, doc = _setup(tmp_path, """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""")
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})

    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert any(i.rule_code == "OBLIG-JUDGE-MISSING" for i in issues)


def test_verify_llm_tag_covered_by_judge_report_passes(tmp_path):
    cfg, doc = _setup(tmp_path, """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""")
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(json.dumps([
        {"subgraph": doc.file_path, "status": "PASS"}
    ]), encoding="utf-8")

    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code.startswith("OBLIG-JUDGE")] == []


def test_stored_judge_failure_is_surfaced(tmp_path):
    cfg, doc = _setup(tmp_path, """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""")
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(json.dumps([
        {"subgraph": doc.file_path, "status": "FAIL"}
    ]), encoding="utf-8")

    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert any(i.rule_code == "OBLIG-JUDGE-FAILED" for i in issues)


def test_gate_can_be_disabled(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    cfg.obligation.enabled = False
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert issues == []


def test_partial_assessment_is_rejected(tmp_path):
    """A discharge rate computed over 15 of 663 sections is not coverage."""
    cfg, doc = _setup(tmp_path, DOC_BODY)
    out = tmp_path / "reports" / "doc_risk_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "total_evaluated": 1,          # only one section triaged
        "assessments": [],
        "doc_hashes": {doc.file_path: doc.content_hash},
    }), encoding="utf-8")

    issues, summary = ObligationVerifier(cfg).verify([doc])
    partial = [i for i in issues if i.rule_code == "OBLIG-ASSESSMENT-PARTIAL"]
    assert len(partial) == 1
    assert summary.sections_assessed == 1
    assert summary.sections_total == len(doc.sections)


def test_full_assessment_is_accepted(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    out = tmp_path / "reports" / "doc_risk_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "total_evaluated": len(doc.sections),
        "assessments": [],
        "doc_hashes": {doc.file_path: doc.content_hash},
    }), encoding="utf-8")

    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-ASSESSMENT-PARTIAL"] == []
