import json

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
    _write_risk_report(
        tmp_path,
        [
            {
                "file_path": doc.file_path,
                "heading": "4.1 アルゴリズム",
                "risk_score": 4,
                "complexity_score": 3,
                "formal_needed": True,
                "recommended_verification": "pyModelChecking",
                "suggested_tags": ["{VERIFY_FORMAL}"],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc])
    skipped = [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"]
    assert len(skipped) == 1
    assert summary.demanded == 1
    assert summary.discharged == 0
    # The error must point at the offending section, not the top of the file.
    assert skipped[0].line == 2


def test_demanded_verification_that_is_tagged_is_discharged(tmp_path):
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_FORMAL}
## 4.1 アルゴリズム
Round-robin scheduling.
""",
    )
    _write_risk_report(
        tmp_path,
        [
            {
                "file_path": doc.file_path,
                "heading": "4.1 アルゴリズム",
                "risk_score": 5,
                "formal_needed": True,
                "recommended_verification": "pyModelChecking",
                "suggested_tags": ["{VERIFY_FORMAL}"],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"] == []
    assert summary.demanded == 1
    assert summary.discharged == 1


def test_llm_recommendation_is_demanded_even_at_risk_3(tmp_path):
    """The heuristic backend hardcodes risk=3 for its LLM_Judge branch (vs.
    risk=4 for formal), so gating solely on `risk >= risk_threshold` (4)
    meant an LLM_Judge recommendation could never actually be demanded --
    every one of them silently bypassed this check, regardless of whether
    {VERIFY_LLM} was ever added. The recommendation itself must count."""
    cfg, doc = _setup(tmp_path, DOC_BODY)
    _write_risk_report(
        tmp_path,
        [
            {
                "file_path": doc.file_path,
                "heading": "4.1 アルゴリズム",
                "risk_score": 3,
                "complexity_score": 3,
                "formal_needed": False,
                "recommended_verification": "llm_judge",
                "suggested_tags": ["{VERIFY_LLM}"],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc])
    skipped = [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"]
    assert len(skipped) == 1
    assert summary.demanded == 1
    assert summary.discharged == 0


def test_low_risk_section_creates_no_obligation(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    _write_risk_report(
        tmp_path,
        [
            {
                "file_path": doc.file_path,
                "heading": "4.1 アルゴリズム",
                "risk_score": 1,
                "formal_needed": False,
                "recommended_verification": "Static",
                "suggested_tags": [],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc])
    assert summary.demanded == 0
    assert [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"] == []


def test_stale_assessment_is_an_error(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    _write_risk_report(
        tmp_path,
        [
            {
                "file_path": doc.file_path,
                "heading": "4.1 アルゴリズム",
                "risk_score": 1,
                "formal_needed": False,
                "recommended_verification": "Static",
                "suggested_tags": [],
            }
        ],
        doc_hashes={doc.file_path: "0000deadbeef"},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc])
    assert any(i.rule_code == "OBLIG-ASSESSMENT-STALE" for i in issues)
    assert summary.stale_documents == [doc.file_path]


def test_verify_llm_tag_without_a_judge_report_is_an_error(tmp_path):
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert any(i.rule_code == "OBLIG-JUDGE-MISSING" for i in issues)


def test_verify_llm_tag_covered_by_judge_report_passes(tmp_path):
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "subgraph": doc.file_path,
                        "status": "PASS",
                        "covered_files": [doc.file_path],
                    }
                ],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code.startswith("OBLIG-JUDGE")] == []


def test_stored_judge_failure_is_surfaced(tmp_path):
    """Real `judge` output is keyword-centric ({"item_label": "{Kw}", "status":
    ...}), not the {"subgraph"|"item"|"target": ...} shape this check used to
    look for -- against real output that mismatch meant `failed` was always
    empty, so no FAIL verdict could ever surface here."""
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text about {LowOverheadSwitch}.
""",
    )
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "item_id": "item:LowOverheadSwitch",
                        "item_label": "{LowOverheadSwitch}",
                        "status": "FAIL",
                        "summary": "does not implement the mechanism",
                        "issues": [],
                        "covered_files": [doc.file_path],
                    },
                ],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    failed = [i for i in issues if i.rule_code == "OBLIG-JUDGE-FAILED"]
    assert len(failed) == 1
    assert "LowOverheadSwitch" in failed[0].message


def test_stored_judge_failure_for_an_unrelated_keyword_is_not_surfaced(tmp_path):
    """A FAIL verdict for a keyword this document never cites must not be
    attributed to it -- the keyword-to-document link has to be real, not
    'any FAIL anywhere in the report'."""
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text about {LowOverheadSwitch}.
""",
    )
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "item_id": "item:SomeOtherKeyword",
                        "item_label": "{SomeOtherKeyword}",
                        "status": "FAIL",
                        "summary": "unrelated failure",
                        "issues": [],
                        "covered_files": [doc.file_path],
                    },
                ],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-JUDGE-FAILED"] == []


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
    out.write_text(
        json.dumps(
            {
                "total_evaluated": 1,  # only one section triaged
                "assessments": [],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, summary = ObligationVerifier(cfg).verify([doc])
    partial = [i for i in issues if i.rule_code == "OBLIG-ASSESSMENT-PARTIAL"]
    assert len(partial) == 1
    assert summary.sections_assessed == 1
    assert summary.sections_total == len(doc.sections)


def test_full_assessment_is_accepted(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    out = tmp_path / "reports" / "doc_risk_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "total_evaluated": len(doc.sections),
                "assessments": [],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-ASSESSMENT-PARTIAL"] == []


def test_mock_generated_assessment_is_rejected(tmp_path):
    """A mock derives obligations from existing tags, so 100% discharge is a tautology."""
    cfg, doc = _setup(tmp_path, DOC_BODY)
    out = tmp_path / "reports" / "doc_risk_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "backend": "mock",
                "total_evaluated": len(doc.sections),
                "assessments": [],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert any(i.rule_code == "OBLIG-ASSESSMENT-NOT-INDEPENDENT" for i in issues)


def test_real_backend_assessment_is_accepted(tmp_path):
    cfg, doc = _setup(tmp_path, DOC_BODY)
    out = tmp_path / "reports" / "doc_risk_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "backend": "sakura",
                "total_evaluated": len(doc.sections),
                "assessments": [],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-ASSESSMENT-NOT-INDEPENDENT"] == []


def test_assessment_without_recorded_backend_is_rejected(tmp_path):
    """Independence cannot be established for an assessment of unknown provenance."""
    cfg, doc = _setup(tmp_path, DOC_BODY)
    out = tmp_path / "reports" / "doc_risk_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "total_evaluated": len(doc.sections),
                "assessments": [],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert any(i.rule_code == "OBLIG-ASSESSMENT-PROVENANCE-UNKNOWN" for i in issues)


def test_judge_verdict_on_an_edited_document_is_rejected_as_stale(tmp_path):
    """The judge report is evidence about a specific text. Once the document
    moves on, the stored verdict describes something that no longer exists.
    This is not hypothetical: the fireball judge report passed
    '{ContextPointerRegister}' with a summary asserting the context pointer
    lived in R7, four commits after R7 had been removed from the specification.
    Nothing detected it, because the report carried no hashes at all."""
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text about {LowOverheadSwitch}.
""",
    )
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(
        json.dumps(
            {
                "results": [{"subgraph": doc.file_path, "status": "PASS"}],
                # Verdict formed against an earlier revision of this document.
                "doc_hashes": {doc.file_path: "hash-of-the-version-that-was-audited"},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    stale = [i for i in issues if i.rule_code == "OBLIG-JUDGE-STALE"]
    assert len(stale) == 1, (
        "a verdict formed against different text must not discharge the obligation"
    )
    assert doc.file_path == stale[0].file_path


def test_judge_report_without_hashes_cannot_discharge_an_obligation(tmp_path):
    """A bare-list judge report (the pre-anchoring format) gives no way to tell
    which specification version it audited, so it must not count as evidence."""
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(json.dumps([{"subgraph": doc.file_path, "status": "PASS"}]), encoding="utf-8")
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-JUDGE-UNANCHORED"], (
        "an unanchored verdict must be rejected, not silently accepted"
    )


def test_a_cleanly_passing_document_counts_as_audited(tmp_path):
    """Coverage used to be inferred by finding the document's path somewhere in
    the report text, which only happens when an issue's `location` names it. A
    document that passed with no issues contributed no such text and was
    reported as never audited -- indistinguishable from a real gap."""
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(
        json.dumps(
            {
                # A clean verdict: no issues, and nothing naming the file except the
                # explicit coverage record.
                "results": [
                    {
                        "item_id": "item:LowOverheadSwitch",
                        "item_label": "{LowOverheadSwitch}",
                        "status": "PASS",
                        "summary": "Consistent.",
                        "issues": [],
                        "covered_files": [doc.file_path],
                    }
                ],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-JUDGE-SKIPPED"] == [], (
        "a document the judge actually covered must not be reported as skipped"
    )


def test_a_document_merely_named_in_someone_elses_issue_is_not_audited(tmp_path):
    """Substring matching over-counted as well as under-counted: a document named
    inside an unrelated keyword's issue prose read as covered. Coverage has to
    come from what the judge was actually given, not from who got mentioned."""
    cfg, doc = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_report(tmp_path, [], doc_hashes={doc.file_path: doc.content_hash})
    judge = tmp_path / "reports" / "doc_judge_report.json"
    judge.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "item_id": "item:Other",
                        "item_label": "{Other}",
                        "status": "WARN",
                        "summary": "Unrelated finding.",
                        # The document is named here, but was never part of this subgraph.
                        "issues": [
                            {
                                "severity": "WARNING",
                                "location": "components/tier1_interface/ipc_router.md",
                                "description": f"contrast with {doc.file_path} which does it differently",
                            }
                        ],
                        "covered_files": ["components/tier1_interface/ipc_router.md"],
                    }
                ],
                "doc_hashes": {doc.file_path: doc.content_hash},
            }
        ),
        encoding="utf-8",
    )
    issues, _ = ObligationVerifier(cfg).verify([doc])
    assert [i for i in issues if i.rule_code == "OBLIG-JUDGE-SKIPPED"], (
        "being mentioned in another keyword's finding is not being audited"
    )
