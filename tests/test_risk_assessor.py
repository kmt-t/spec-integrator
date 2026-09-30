import json

import pytest
from spec_integrator.config import Config, LLMBackendConfig
from spec_integrator.judge.risk_assessor import KeywordRiskAssessment, RiskAssessmentReport
from spec_integrator.models import ParsedDocument, ParsedSection


def test_risk_assessment_report_markdown():
    report = RiskAssessmentReport(
        assessments=[
            KeywordRiskAssessment(
                item_id="item:CSPCommunication",
                keyword="CSPCommunication",
                file_path="components/tier1_core/os_coos.md",
                tier=1,
                complexity_score=4,
                risk_score=5,
                summary="High concurrency complexity in CSP handoff.",
            ),
            KeywordRiskAssessment(
                item_id="item:StaticConstants",
                keyword="StaticConstants",
                file_path="components/tier1_core/system_config.md",
                tier=1,
                complexity_score=1,
                risk_score=1,
                summary="Simple declarative constants.",
            ),
        ],
        total_evaluated=2,
        high_risk_count=1,
    )
    md = report.to_markdown(risk_threshold=4)
    assert "設計複雑度 & リスク評価レポート" in md
    assert "高リスクキーワード" in md
    assert "`components/tier1_core/os_coos.md`" in md
    assert "`{CSPCommunication}`" in md


def test_risk_assessment_report_sorts_by_risk_only():
    """Sort order is risk_score alone -- complexity is not a tie-breaker."""
    report = RiskAssessmentReport(
        assessments=[
            KeywordRiskAssessment(
                item_id="item:A",
                keyword="A",
                file_path="a.md",
                tier=1,
                complexity_score=5,
                risk_score=2,
            ),
            KeywordRiskAssessment(
                item_id="item:B",
                keyword="B",
                file_path="b.md",
                tier=1,
                complexity_score=1,
                risk_score=4,
            ),
        ],
        total_evaluated=2,
        high_risk_count=1,
    )
    md = report.to_markdown(risk_threshold=4)
    # B has the lower complexity score but the higher risk score, so it must
    # be listed first in the full ranking table.
    assert md.index("`{B}`") < md.index("`{A}`")


@pytest.mark.parametrize(
    ("context_window_tokens", "max_section_chars"), [(8192, 500), (16384, 1000)]
)
def test_nimble_risk_context_fits_request_limit_and_reports_sampling(
    monkeypatch, context_window_tokens, max_section_chars
):
    from spec_integrator.judge.risk_assessor import RiskAssessor

    config = Config()
    config.llm_judge.backends["nimble"] = LLMBackendConfig(
        endpoint="http://localhost:11434/v1/systemone",
        model="nimble",
        requires_api_key=False,
        context_window_tokens=context_window_tokens,
    )
    documents = []
    definition_ids = []
    reference_ids = []
    for index in range(40):
        file_path = f"docs/context_{index}.md"
        section_id = f"sec:{file_path}#Context"
        section = ParsedSection(
            section_id=section_id,
            file_path=file_path,
            heading="Context",
            level=2,
            line_start=1,
            line_end=30,
            body_text="設計条件と相互作用を含む説明。" * 300,
        )
        documents.append(
            ParsedDocument(
                file_path=file_path,
                full_path=None,
                tier=1,
                component=f"context_{index}",
                content=section.body_text,
                content_hash=f"hash_{index}",
                sections=[section],
            )
        )
        if index < 10:
            definition_ids.append(section_id)
        reference_ids.append(section_id)

    captured = {}

    def fake_submit(self, sheet, model, backend):
        captured["sheet"] = sheet
        captured["model"] = model
        captured["backend"] = backend
        return {
            "answers": {
                "complexity": {"score": 2.0},
                "design_risk": {"score": 3.0},
            }
        }

    monkeypatch.setattr(RiskAssessor, "_submit_checksheet", fake_submit)
    result = RiskAssessor(config)._assess_single_keyword(
        {
            "item_id": "item:ContextBudget",
            "item_label": "{ContextBudget}",
            "defined_in": definition_ids,
            "referenced_in": reference_ids,
        },
        documents,
        backend="nimble",
    )

    sheet = captured["sheet"]
    payload = {
        "model": "nimble",
        "state": sheet.state,
        "questions": sheet.questions,
    }
    body_size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    assert captured["backend"] == "nimble"
    assert len(sheet.state["definition_sections"]) == 4
    assert len(sheet.state["referencing_sections"]) == 4
    assert body_size <= 56 * 1024
    note = sheet.state["context_limit_note"]
    assert "64 KiB request body" in note
    assert int(note.split("each section is capped at ")[1].split()[0]) <= max_section_chars
    assert "Context was limited" in result.summary
