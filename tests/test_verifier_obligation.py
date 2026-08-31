from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.graph import DocGraphBuilder
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.obligation import ObligationVerifier


def _setup(tmp_path, body):
    docs_dir = tmp_path / "docs"
    target = docs_dir / "components" / "tier1_core" / "os_scheduler.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    cfg = Config()
    cfg.config_dir = tmp_path
    doc = MarkdownParser(cfg).parse_file(target, docs_dir)
    db = DocAuditDB(":memory:")
    return cfg, doc, db


def _write_risk_assessment(db, assessments, doc_hashes=None, backend="sakura"):
    db.replace_risk_assessments(assessments, backend)
    if doc_hashes is not None:
        db.set_assessed_doc_hashes("risk_assessment", doc_hashes)
    db.commit()


def _write_judge_results(db, results, doc_hashes=None, backend="sakura"):
    db.replace_judge_results(results, backend)
    if doc_hashes is not None:
        db.set_assessed_doc_hashes("judge", doc_hashes)
    db.commit()


def _write_document_judge_results(db, results, doc_hashes=None, backend="sakura"):
    db.replace_document_judge_results(results, backend)
    if doc_hashes is not None:
        db.set_assessed_doc_hashes("document_judge", doc_hashes)
    db.commit()


DOC_BODY = """# Scheduler
## 4.1 アルゴリズム
Round-robin scheduling with interrupt wakeup.
"""


def test_missing_assessment_is_an_error(tmp_path):
    cfg, doc, db = _setup(tmp_path, DOC_BODY)
    issues, summary = ObligationVerifier(cfg).verify([doc], db=db)
    assert any(i.rule_code == "OBLIG-ASSESSMENT-MISSING" for i in issues)


def test_high_risk_keyword_without_the_demanded_tag_is_an_error(tmp_path):
    cfg, doc, db = _setup(tmp_path, DOC_BODY)
    _write_risk_assessment(
        db,
        [
            {
                "item_id": "item:RoundRobinScheduling",
                "keyword": "RoundRobinScheduling",
                "file_path": doc.file_path,
                "risk_score": 4,
                "complexity_score": 3,
                "line": 2,
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc], db=db)
    skipped = [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"]
    assert len(skipped) == 1
    assert summary.demanded == 1
    assert summary.discharged == 0
    # The error must point at the recorded definition line, not the top of the file.
    assert skipped[0].line == 2


def test_demanded_verification_that_is_tagged_is_discharged(tmp_path):
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Round-robin scheduling.
""",
    )
    _write_risk_assessment(
        db,
        [
            {
                "item_id": "item:RoundRobinScheduling",
                "keyword": "RoundRobinScheduling",
                "file_path": doc.file_path,
                "risk_score": 5,
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc], db=db)
    assert [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"] == []
    assert summary.demanded == 1
    assert summary.discharged == 1


def test_low_risk_keyword_creates_no_obligation(tmp_path):
    cfg, doc, db = _setup(tmp_path, DOC_BODY)
    _write_risk_assessment(
        db,
        [
            {
                "item_id": "item:RoundRobinScheduling",
                "keyword": "RoundRobinScheduling",
                "file_path": doc.file_path,
                "risk_score": 1,
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc], db=db)
    assert summary.demanded == 0
    assert [i for i in issues if i.rule_code == "OBLIG-VERIFICATION-SKIPPED"] == []


def test_stale_assessment_is_an_error(tmp_path):
    cfg, doc, db = _setup(tmp_path, DOC_BODY)
    _write_risk_assessment(
        db,
        [
            {
                "item_id": "item:RoundRobinScheduling",
                "keyword": "RoundRobinScheduling",
                "file_path": doc.file_path,
                "risk_score": 1,
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: "0000deadbeef"},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc], db=db)
    assert any(i.rule_code == "OBLIG-ASSESSMENT-STALE" for i in issues)
    assert summary.stale_documents == [doc.file_path]


def test_verify_llm_tag_without_a_judge_report_is_an_error(tmp_path):
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert any(i.rule_code == "OBLIG-JUDGE-MISSING" for i in issues)


def test_verify_llm_tag_covered_by_judge_report_passes(tmp_path):
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:Scheduler",
                "item_label": "{Scheduler}",
                "status": "PASS",
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    _write_document_judge_results(
        db,
        [
            {
                "item_id": doc.file_path,
                "item_label": doc.file_path,
                "status": "PASS",
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert [i for i in issues if i.rule_code.startswith("OBLIG-JUDGE")] == []
    assert [i for i in issues if i.rule_code.startswith("OBLIG-DOC-JUDGE")] == []


def test_verify_llm_tag_without_a_document_judge_report_is_an_error(tmp_path):
    """Subgraph coverage alone is not enough -- the whole-document audit is
    an independent check, so a document tagged {VERIFY_LLM} that was never
    document-judged must fail even if its subgraph judge coverage is clean."""
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:Scheduler",
                "item_label": "{Scheduler}",
                "status": "PASS",
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc], db=db)
    assert any(i.rule_code == "OBLIG-DOC-JUDGE-MISSING" for i in issues)
    assert summary.document_judge_missing == [doc.file_path]


def test_document_judge_failure_is_surfaced(tmp_path):
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:Scheduler",
                "item_label": "{Scheduler}",
                "status": "PASS",
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    _write_document_judge_results(
        db,
        [
            {
                "item_id": doc.file_path,
                "item_label": doc.file_path,
                "status": "FAIL",
                "summary": "self-contradicts on the wakeup latency figure",
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    failed = [i for i in issues if i.rule_code == "OBLIG-DOC-JUDGE-FAILED"]
    assert len(failed) == 1
    assert failed[0].file_path == doc.file_path


def test_document_judge_verdict_on_an_edited_document_is_rejected_as_stale(tmp_path):
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:Scheduler",
                "item_label": "{Scheduler}",
                "status": "PASS",
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    _write_document_judge_results(
        db,
        [
            {
                "item_id": doc.file_path,
                "item_label": doc.file_path,
                "status": "PASS",
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: "hash-of-the-version-that-was-audited"},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    stale = [i for i in issues if i.rule_code == "OBLIG-DOC-JUDGE-STALE"]
    assert len(stale) == 1


def test_stored_judge_failure_is_surfaced(tmp_path):
    """A FAIL verdict for a keyword this document cites must surface as an error."""
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text about {LowOverheadSwitch}.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:LowOverheadSwitch",
                "item_label": "{LowOverheadSwitch}",
                "status": "FAIL",
                "summary": "does not implement the mechanism",
                "covered_files": [doc.file_path],
            },
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    failed = [i for i in issues if i.rule_code == "OBLIG-JUDGE-FAILED"]
    assert len(failed) == 1
    assert "LowOverheadSwitch" in failed[0].message


def test_stored_judge_failure_for_an_unrelated_keyword_is_not_surfaced(tmp_path):
    """A FAIL verdict for a keyword this document never cites must not be
    attributed to it -- the keyword-to-document link has to be real, not
    'any FAIL anywhere in the report'."""
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text about {LowOverheadSwitch}.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:SomeOtherKeyword",
                "item_label": "{SomeOtherKeyword}",
                "status": "FAIL",
                "summary": "unrelated failure",
                "covered_files": [doc.file_path],
            },
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert [i for i in issues if i.rule_code == "OBLIG-JUDGE-FAILED"] == []


def test_gate_can_be_disabled(tmp_path):
    cfg, doc, db = _setup(tmp_path, DOC_BODY)
    cfg.obligation.enabled = False
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert issues == []


def test_partial_assessment_is_rejected(tmp_path):
    """A discharge rate computed over 1 of 2 keywords is not coverage."""
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler
## 4.1 アルゴリズム
Round-robin scheduling, see {KeywordA}.
## 4.2 別アルゴリズム
Priority scheduling, see {KeywordB}.
""",
    )
    graph = DocGraphBuilder(cfg).build([doc], tmp_path / "docs")
    _write_risk_assessment(
        db,
        [
            {
                "item_id": "item:KeywordA",
                "keyword": "KeywordA",
                "file_path": doc.file_path,
                "risk_score": 1,
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, summary = ObligationVerifier(cfg).verify([doc], graph, db)
    partial = [i for i in issues if i.rule_code == "OBLIG-ASSESSMENT-PARTIAL"]
    assert len(partial) == 1
    assert summary.keywords_assessed == 1
    assert summary.keywords_total == 2


def test_full_assessment_is_accepted(tmp_path):
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler
## 4.1 アルゴリズム
Round-robin scheduling, see {KeywordA}.
""",
    )
    graph = DocGraphBuilder(cfg).build([doc], tmp_path / "docs")
    _write_risk_assessment(
        db,
        [
            {
                "item_id": "item:KeywordA",
                "keyword": "KeywordA",
                "file_path": doc.file_path,
                "risk_score": 1,
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], graph, db)
    assert [i for i in issues if i.rule_code == "OBLIG-ASSESSMENT-PARTIAL"] == []


def test_mock_generated_assessment_is_rejected(tmp_path):
    """A mock derives obligations from existing tags, so 100% discharge is a tautology."""
    cfg, doc, db = _setup(tmp_path, DOC_BODY)
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash}, backend="mock")
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert any(i.rule_code == "OBLIG-ASSESSMENT-NOT-INDEPENDENT" for i in issues)


def test_real_backend_assessment_is_accepted(tmp_path):
    cfg, doc, db = _setup(tmp_path, DOC_BODY)
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash}, backend="sakura")
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert [i for i in issues if i.rule_code == "OBLIG-ASSESSMENT-NOT-INDEPENDENT"] == []


def test_assessment_without_recorded_backend_is_rejected(tmp_path):
    """Independence cannot be established for an assessment of unknown provenance."""
    cfg, doc, db = _setup(tmp_path, DOC_BODY)
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash}, backend="")
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert any(i.rule_code == "OBLIG-ASSESSMENT-PROVENANCE-UNKNOWN" for i in issues)


def test_judge_verdict_on_an_edited_document_is_rejected_as_stale(tmp_path):
    """The judge verdict is evidence about a specific text. Once the document
    moves on, the stored verdict describes something that no longer exists.
    This is not hypothetical: the fireball judge report passed
    '{ContextPointerRegister}' with a summary asserting the context pointer
    lived in R7, four commits after R7 had been removed from the specification.
    Nothing detected it, because the report carried no hashes at all."""
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text about {LowOverheadSwitch}.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:Scheduler",
                "item_label": "{Scheduler}",
                "status": "PASS",
                "covered_files": [doc.file_path],
            }
        ],
        # Verdict formed against an earlier revision of this document.
        doc_hashes={doc.file_path: "hash-of-the-version-that-was-audited"},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    stale = [i for i in issues if i.rule_code == "OBLIG-JUDGE-STALE"]
    assert len(stale) == 1, (
        "a verdict formed against different text must not discharge the obligation"
    )
    assert doc.file_path == stale[0].file_path


def test_judge_report_without_hashes_cannot_discharge_an_obligation(tmp_path):
    """A judge verdict recorded with no document hashes gives no way to tell
    which specification version it audited, so it must not count as evidence."""
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [{"item_id": "item:Scheduler", "item_label": "{Scheduler}", "status": "PASS"}],
        doc_hashes=None,
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert [i for i in issues if i.rule_code == "OBLIG-JUDGE-UNANCHORED"], (
        "an unanchored verdict must be rejected, not silently accepted"
    )


def test_a_cleanly_passing_document_counts_as_audited(tmp_path):
    """Coverage must come from the verdict's own recorded covered_files, not
    from finding the document's path somewhere in issue text -- a document that
    passed with no issues contributes no such text and must not read as never
    audited, indistinguishable from a real gap."""
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:LowOverheadSwitch",
                "item_label": "{LowOverheadSwitch}",
                "status": "PASS",
                "summary": "Consistent.",
                "covered_files": [doc.file_path],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert [i for i in issues if i.rule_code == "OBLIG-JUDGE-SKIPPED"] == [], (
        "a document the judge actually covered must not be reported as skipped"
    )


def test_a_document_merely_named_in_someone_elses_issue_is_not_audited(tmp_path):
    """Coverage has to come from what the judge was actually given (covered_files),
    not from a document merely being named inside an unrelated keyword's finding."""
    cfg, doc, db = _setup(
        tmp_path,
        """# Scheduler {VERIFY_LLM}
## 4.1 アルゴリズム
Text.
""",
    )
    _write_risk_assessment(db, [], doc_hashes={doc.file_path: doc.content_hash})
    _write_judge_results(
        db,
        [
            {
                "item_id": "item:Other",
                "item_label": "{Other}",
                "status": "WARN",
                "summary": f"contrast with {doc.file_path} which does it differently",
                # The document is named in the summary above, but was never part
                # of this subgraph -- covered_files says what was actually audited.
                "covered_files": ["components/tier1_interface/ipc_router.md"],
            }
        ],
        doc_hashes={doc.file_path: doc.content_hash},
    )
    issues, _ = ObligationVerifier(cfg).verify([doc], db=db)
    assert [i for i in issues if i.rule_code == "OBLIG-JUDGE-SKIPPED"], (
        "being mentioned in another keyword's finding is not being audited"
    )
