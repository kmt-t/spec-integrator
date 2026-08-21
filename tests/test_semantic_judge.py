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
