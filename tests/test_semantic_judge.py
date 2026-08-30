from spec_integrator.config import Config
from spec_integrator.judge import SemanticJudge
from spec_integrator.parser import ParsedDocument, ParsedSection


def test_semantic_judge_mock():
    config = Config()
    judge = SemanticJudge(config)
    subgraphs = [
        {
            "item_id": "req:RoleBasedAccessControl",
            "item_label": "RoleBasedAccessControl",
            "defined_in": ["sec:requires/requirement_list.md#RoleBasedAccessControl"],
            "referenced_in": ["sec:components/tier1_interface/ipc_router.md#コンセプト"],
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
                    keywords=["RoleBasedAccessControl"],
                )
            ],
            all_keywords=["RoleBasedAccessControl"],
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
                    keywords=["RoleBasedAccessControl"],
                )
            ],
            all_keywords=["RoleBasedAccessControl"],
        ),
    ]
    results = judge.judge_subgraphs(subgraphs, documents, backend="mock")
    assert len(results) == 1
    assert results[0].status == "PASS"
    assert results[0].item_label == "RoleBasedAccessControl"


def _judge_with(raw_response):
    """Drives the real evaluation path with a canned backend response."""
    from spec_integrator.judge.semantic_judge import SemanticJudge

    judge = SemanticJudge(Config())
    judge._call_sakura = lambda prompt, model: raw_response
    sg = {
        "item_id": "item:{K}",
        "item_label": "{K}",
        "defined_in": [],
        "referenced_in": [],
    }
    return judge._evaluate_single_subgraph(sg, [], backend="sakura", model=None)


def test_missing_status_field_is_not_a_pass():
    """A verdict the model never stated must not default to PASS."""
    res = _judge_with('{"summary": "looks fine", "issues": []}')
    assert res.status == "FAIL"
    assert any("no 'status' field" in str(i.get("description", "")) for i in res.issues)


def test_pass_with_error_issues_is_downgraded():
    """An audit listing blocking issues has not passed, whatever it says."""
    res = _judge_with(
        '{"status": "PASS", "summary": "x", '
        '"issues": [{"severity": "ERROR", "description": "contradiction"}]}'
    )
    assert res.status == "FAIL"


def test_clean_pass_is_preserved():
    res = _judge_with('{"status": "PASS", "summary": "ok", "issues": []}')
    assert res.status == "PASS"


def test_transient_empty_response_is_retried_and_recovers(monkeypatch):
    """_call_sakura already retries transport failures (non-200, timeout). What
    it cannot catch is a 200 response whose body is empty or malformed -- that
    surfaces one layer up, as a JSON parse failure here. A single flaky response
    from a backend under load must not turn a real audit into 'cannot verify'."""

    monkeypatch.setattr("time.sleep", lambda *_: None)
    judge = SemanticJudge(Config())
    calls = {"n": 0}

    def flaky(prompt, model):
        calls["n"] += 1
        if calls["n"] == 1:
            return ""  # empty body: json.loads("") -> "Expecting value" parse error
        return '{"status": "PASS", "summary": "ok on retry", "issues": []}'

    judge._call_sakura = flaky
    sg = {
        "item_id": "item:{K}",
        "item_label": "{K}",
        "defined_in": [],
        "referenced_in": [],
    }
    res = judge._evaluate_single_subgraph(sg, [], backend="sakura", model=None)
    assert calls["n"] == 2, "must retry after the empty response rather than giving up immediately"
    assert res.status == "PASS"
    assert res.summary == "ok on retry"


def test_persistent_failure_exhausts_retries_and_reports_it(monkeypatch):
    """After genuinely repeated failures the gate must still fail closed -- and
    say how many attempts it made, not just repeat the last raw parse error."""

    monkeypatch.setattr("time.sleep", lambda *_: None)
    judge = SemanticJudge(Config())
    calls = {"n": 0}

    def always_empty(prompt, model):
        calls["n"] += 1
        return ""

    judge._call_sakura = always_empty
    sg = {
        "item_id": "item:{K}",
        "item_label": "{K}",
        "defined_in": [],
        "referenced_in": [],
    }
    res = judge._evaluate_single_subgraph(sg, [], backend="sakura", model=None)
    assert calls["n"] == 3, "must attempt exactly 3 times, not loop forever or give up early"
    assert res.status == "FAIL"
    assert "3 attempts" in res.summary


def _two_keyword_fixture():
    """Two independent requirement/design pairs sharing one document set,
    so a section-level change filter can be exercised without a real corpus."""
    documents = [
        ParsedDocument(
            file_path="requires/requirement_list.md",
            full_path=None,
            tier=0,
            component="requires",
            content_hash="req-h",
            content="# Requirements\n## A\nDefines A.\n## B\nDefines B.",
            sections=[
                ParsedSection(
                    section_id="sec:requires/requirement_list.md#A",
                    file_path="requires/requirement_list.md",
                    heading="A",
                    level=2,
                    line_start=2,
                    line_end=3,
                    body_text="Defines A.",
                    keywords=["A"],
                ),
                ParsedSection(
                    section_id="sec:requires/requirement_list.md#B",
                    file_path="requires/requirement_list.md",
                    heading="B",
                    level=2,
                    line_start=4,
                    line_end=5,
                    body_text="Defines B.",
                    keywords=["B"],
                ),
            ],
            all_keywords=["A", "B"],
        ),
        ParsedDocument(
            file_path="components/design.md",
            full_path=None,
            tier=1,
            component="design",
            content_hash="design-h",
            content="# Design\n## UseA\nUses {A}.\n## UseB\nUses {B}.",
            sections=[
                ParsedSection(
                    section_id="sec:components/design.md#UseA",
                    file_path="components/design.md",
                    heading="UseA",
                    level=2,
                    line_start=2,
                    line_end=3,
                    body_text="Uses {A}.",
                    keywords=["A"],
                ),
                ParsedSection(
                    section_id="sec:components/design.md#UseB",
                    file_path="components/design.md",
                    heading="UseB",
                    level=2,
                    line_start=4,
                    line_end=5,
                    body_text="Uses {B}.",
                    keywords=["B"],
                ),
            ],
            all_keywords=["A", "B"],
        ),
    ]
    subgraphs = [
        {
            "item_id": "req:A",
            "item_label": "A",
            "defined_in": ["sec:requires/requirement_list.md#A"],
            "referenced_in": ["sec:components/design.md#UseA"],
        },
        {
            "item_id": "req:B",
            "item_label": "B",
            "defined_in": ["sec:requires/requirement_list.md#B"],
            "referenced_in": ["sec:components/design.md#UseB"],
        },
    ]
    return subgraphs, documents


def test_changed_sections_scopes_to_touched_subgraphs_only():
    """Only the keyword whose definition or reference section is in the
    changed set should be audited -- the point of --changed-only is to avoid
    re-running the untouched half of the corpus."""
    config = Config()
    judge = SemanticJudge(config)
    subgraphs, documents = _two_keyword_fixture()
    # Only B's referencing section changed; A's pair is untouched.
    report = judge.judge_subgraphs(
        subgraphs,
        documents,
        backend="mock",
        exhaustive=True,
        changed_sections={"sec:components/design.md#UseB"},
    )
    assert report.total_evaluated == 1
    assert report.results[0].item_label == "B"


def test_changed_sections_none_audits_everything():
    """Passing no filter (the default) must not change existing behaviour --
    changed_sections=None means 'no scoping', not 'scope to nothing'."""
    config = Config()
    judge = SemanticJudge(config)
    subgraphs, documents = _two_keyword_fixture()
    report = judge.judge_subgraphs(subgraphs, documents, backend="mock", exhaustive=True)
    assert report.total_evaluated == 2


def test_changed_sections_empty_set_audits_nothing():
    """An empty (but non-None) changed set means nothing changed -- distinct
    from None, which must not be conflated with 'nothing selected'."""
    config = Config()
    judge = SemanticJudge(config)
    subgraphs, documents = _two_keyword_fixture()
    report = judge.judge_subgraphs(
        subgraphs,
        documents,
        backend="mock",
        exhaustive=True,
        changed_sections=set(),
    )
    assert report.total_evaluated == 0


def test_changed_definition_side_also_selects_the_subgraph():
    """The filter must not silently assume only the reference side moves --
    a keyword's own definition changing must select it too, since which side
    changed is exactly the distinction --changed-only exists to not require."""
    config = Config()
    judge = SemanticJudge(config)
    subgraphs, documents = _two_keyword_fixture()
    report = judge.judge_subgraphs(
        subgraphs,
        documents,
        backend="mock",
        exhaustive=True,
        changed_sections={"sec:requires/requirement_list.md#A"},
    )
    assert report.total_evaluated == 1
    assert report.results[0].item_label == "A"


def test_judge_prompt_template_includes_redundancy_and_layered_criteria():
    """Verify that the judge prompt explicitly instructs the LLM to audit redundant duplication
    while permitting multi-perspective and multi-layer descriptions."""
    from spec_integrator.judge.semantic_judge import JUDGE_PROMPT_TEMPLATE

    assert "Redundancy & Duplication Audit" in JUDGE_PROMPT_TEMPLATE
    assert (
        "PERMITTED AND ENCOURAGED (Legitimate Layered / Multi-perspective Descriptions)"
        in JUDGE_PROMPT_TEMPLATE
    )
    assert "FLAGGED AS REDUNDANT" in JUDGE_PROMPT_TEMPLATE
