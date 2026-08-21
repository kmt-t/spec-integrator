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
