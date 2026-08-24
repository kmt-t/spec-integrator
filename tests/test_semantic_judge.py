import pytest
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument, ParsedSection
from spec_integrator.judge import SemanticJudge, JudgeResult


def test_semantic_judge_mock():
    config = Config()
    judge = SemanticJudge(config)

    subgraphs = [
        {
            "item_id": "req:RoleBasedAccessControl",
            "item_label": "RoleBasedAccessControl",
            "defined_in": ["sec:requires/requirement_list.md#RoleBasedAccessControl"],
            "referenced_in": ["sec:components/tier1_interface/ipc_router.md#コンセプト"]
        }
    ]
    documents = [
        ParsedDocument(
            file_path="requires/requirement_list.md",
            full_path=None,
            tier=0,
            component="requires",
            content="# Requirement List\n## RoleBasedAccessControl\nStatic URI matrix access control.",
            content_hash="h1",
            sections=[
                ParsedSection(
                    section_id="sec:requires/requirement_list.md#RoleBasedAccessControl",
                    file_path="requires/requirement_list.md",
                    heading="RoleBasedAccessControl",
                    level=2,
                    line_start=2,
                    line_end=4,
                    body_text="Static URI matrix access control.",
                    keywords=["RoleBasedAccessControl"]
                )
            ],
            all_keywords=["RoleBasedAccessControl"]
        ),
        ParsedDocument(
            file_path="components/tier1_interface/ipc_router.md",
            full_path=None,
            tier=1,
            component="ipc_router",
            content="# IPC Router\n## コンセプト\nUses {RoleBasedAccessControl} for URI dispatch.",
            content_hash="h2",
            sections=[
                ParsedSection(
                    section_id="sec:components/tier1_interface/ipc_router.md#コンセプト",
                    file_path="components/tier1_interface/ipc_router.md",
                    heading="コンセプト",
                    level=2,
                    line_start=2,
                    line_end=4,
                    body_text="Uses {RoleBasedAccessControl} for URI dispatch.",
                    keywords=["RoleBasedAccessControl"]
                )
            ],
            all_keywords=["RoleBasedAccessControl"]
        )
    ]

    results = judge.judge_subgraphs(subgraphs, documents, backend="mock")
    assert len(results) == 1
    assert results[0].status == "PASS"
    assert results[0].item_label == "RoleBasedAccessControl"


def _judge_with(raw_response):
    """Drives the real evaluation path with a canned backend response."""
    from spec_integrator.judge.semantic_judge import SemanticJudge
    from spec_integrator.config import Config

    judge = SemanticJudge(Config())
    judge._call_sakura = lambda prompt, model: raw_response
    sg = {"item_id": "item:{K}", "item_label": "{K}",
          "defined_in": [], "referenced_in": []}
    return judge._evaluate_single_subgraph(sg, [], backend="sakura", model=None)


def test_missing_status_field_is_not_a_pass():
    """A verdict the model never stated must not default to PASS."""
    res = _judge_with('{"summary": "looks fine", "issues": []}')
    assert res.status == "FAIL"
    assert any("no 'status' field" in str(i.get("description", "")) for i in res.issues)


def test_pass_with_error_issues_is_downgraded():
    """An audit listing blocking issues has not passed, whatever it says."""
    res = _judge_with('{"status": "PASS", "summary": "x", '
                      '"issues": [{"severity": "ERROR", "description": "contradiction"}]}')
    assert res.status == "FAIL"


def test_clean_pass_is_preserved():
    res = _judge_with('{"status": "PASS", "summary": "ok", "issues": []}')
    assert res.status == "PASS"


def test_transient_empty_response_is_retried_and_recovers(monkeypatch):
    """_call_sakura already retries transport failures (non-200, timeout). What
    it cannot catch is a 200 response whose body is empty or malformed -- that
    surfaces one layer up, as a JSON parse failure here. A single flaky response
    from a backend under load must not turn a real audit into 'cannot verify'."""
    from spec_integrator.judge.semantic_judge import SemanticJudge
    from spec_integrator.config import Config

    monkeypatch.setattr("time.sleep", lambda *_: None)
    judge = SemanticJudge(Config())
    calls = {"n": 0}

    def flaky(prompt, model):
        calls["n"] += 1
        if calls["n"] == 1:
            return ""  # empty body: json.loads("") -> "Expecting value" parse error
        return '{"status": "PASS", "summary": "ok on retry", "issues": []}'

    judge._call_sakura = flaky
    sg = {"item_id": "item:{K}", "item_label": "{K}", "defined_in": [], "referenced_in": []}
    res = judge._evaluate_single_subgraph(sg, [], backend="sakura", model=None)

    assert calls["n"] == 2, "must retry after the empty response rather than giving up immediately"
    assert res.status == "PASS"
    assert res.summary == "ok on retry"


def test_persistent_failure_exhausts_retries_and_reports_it(monkeypatch):
    """After genuinely repeated failures the gate must still fail closed -- and
    say how many attempts it made, not just repeat the last raw parse error."""
    from spec_integrator.judge.semantic_judge import SemanticJudge
    from spec_integrator.config import Config

    monkeypatch.setattr("time.sleep", lambda *_: None)
    judge = SemanticJudge(Config())
    calls = {"n": 0}

    def always_empty(prompt, model):
        calls["n"] += 1
        return ""

    judge._call_sakura = always_empty
    sg = {"item_id": "item:{K}", "item_label": "{K}", "defined_in": [], "referenced_in": []}
    res = judge._evaluate_single_subgraph(sg, [], backend="sakura", model=None)

    assert calls["n"] == 3, "must attempt exactly 3 times, not loop forever or give up early"
    assert res.status == "FAIL"
    assert "3 attempts" in res.summary
