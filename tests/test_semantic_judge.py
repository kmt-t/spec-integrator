from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.graph import DocumentIsland
from spec_integrator.judge import SemanticJudge, UnifiedReviewEngine
from spec_integrator.parser import ParsedDocument, ParsedSection


def _create_sample_doc(file_path: str = "components/test.md") -> ParsedDocument:
    return ParsedDocument(
        file_path=file_path,
        full_path=None,
        tier=1,
        component="test",
        content="# Test Component\n## Intro\nSome introduction text.",
        content_hash="hash123",
        sections=[
            ParsedSection(
                section_id=f"sec:{file_path}#Intro",
                file_path=file_path,
                heading="Intro",
                level=2,
                line_start=2,
                line_end=4,
                body_text="Some introduction text.",
                keywords=["TestKW"],
            )
        ],
        all_keywords=["TestKW"],
    )


def test_semantic_judge_islands_mock():
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    judge = SemanticJudge(config)
    doc = _create_sample_doc()
    island = DocumentIsland(
        island_id="island_01",
        name="Test Island",
        file_paths=[doc.file_path],
        section_ids=[s.section_id for s in doc.sections],
        keywords=["TestKW"],
        total_sections=1,
        total_docs=1,
    )

    report = judge.judge_islands([island], [doc], backend="mock")
    assert report.total_evaluated == 1
    assert report.results[0].status == "PASS"
    assert report.results[0].item_label == "Test Island"


def test_semantic_judge_documents_mock():
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    judge = SemanticJudge(config)
    doc = _create_sample_doc()

    report = judge.judge_documents([doc], backend="mock", exhaustive=True)
    assert report.total_evaluated == 1
    assert report.results[0].status == "PASS"
    assert report.results[0].item_label == doc.file_path


def _judge_with_raw_response(raw_response: str):
    """Drives the real evaluation path with a canned backend response."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    reviewer = UnifiedReviewEngine(config)
    reviewer._call_sakura = lambda prompt, model: raw_response

    doc = _create_sample_doc()
    return reviewer.review_single_document(doc, backend="sakura")


def test_missing_status_field_is_not_a_pass():
    """A verdict the model never stated must not default to PASS."""
    res = _judge_with_raw_response('{"summary": "looks fine", "issues": []}')
    assert res.status == "FAIL"
    assert any("no 'status' field" in str(i.get("description", "")) for i in res.issues)


def test_pass_with_error_issues_is_downgraded():
    """An audit listing blocking issues has not passed, whatever it says."""
    res = _judge_with_raw_response(
        '{"status": "PASS", "summary": "x", '
        '"issues": [{"severity": "ERROR", "description": "contradiction"}]}'
    )
    assert res.status == "FAIL"


def test_clean_pass_is_preserved():
    res = _judge_with_raw_response('{"status": "PASS", "summary": "ok", "issues": []}')
    assert res.status == "PASS"


def test_transient_empty_response_is_retried_and_recovers(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    reviewer = UnifiedReviewEngine(config)
    calls = {"n": 0}

    def flaky(prompt, model):
        calls["n"] += 1
        if calls["n"] == 1:
            return ""  # empty body: json.loads("") -> "Expecting value" parse error
        return '{"status": "PASS", "summary": "ok on retry", "issues": []}'

    reviewer._call_sakura = flaky
    doc = _create_sample_doc()
    res = reviewer.review_single_document(doc, backend="sakura")
    assert calls["n"] == 2, "must retry after the empty response rather than giving up immediately"
    assert res.status == "PASS"
    assert res.summary == "ok on retry"


def test_persistent_failure_exhausts_retries_and_reports_it(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    reviewer = UnifiedReviewEngine(config)
    calls = {"n": 0}

    def always_empty(prompt, model):
        calls["n"] += 1
        return ""

    reviewer._call_sakura = always_empty
    doc = _create_sample_doc()
    res = reviewer.review_single_document(doc, backend="sakura")
    assert calls["n"] == 3, "must attempt exactly 3 times, not loop forever or give up early"
    assert res.status == "FAIL"
    assert "3 attempts" in res.summary


def test_judge_prompt_includes_redundancy_and_layered_criteria():
    """Verify that the redundancy check configured in spec-integrator.yaml instructs the LLM
    to audit redundant duplication while permitting multi-perspective descriptions."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    rule = next((r for r in config.llm_judge.checks if r.id == "redundancy_and_duplication"), None)
    assert rule is not None
    prompt_text = rule.get_prompt_text(config.config_dir)
    assert "Redundancy & Duplication Audit" in prompt_text
    assert (
        "PERMITTED AND ENCOURAGED (Legitimate Layered / Multi-perspective Descriptions)"
        in prompt_text
    )
    assert "FLAGGED AS REDUNDANT" in prompt_text
