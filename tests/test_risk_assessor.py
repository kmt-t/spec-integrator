from spec_integrator.parser import ParsedDocument, ParsedSection
from spec_integrator.judge.risk_assessor import RiskAssessor, SectionRiskAssessment, RiskAssessmentReport


def test_risk_assessment_report_markdown():
    report = RiskAssessmentReport(
        assessments=[
            SectionRiskAssessment(
                section_id="sec:os_coos.md#CSP通信",
                file_path="components/tier1_core/os_coos.md",
                heading="CSP通信",
                tier=1,
                complexity_score=4,
                risk_score=5,
                formal_needed=True,
                recommended_verification="pyModelChecking",
                suggested_tags=["{VERIFY_FORMAL}"],
                risk_factors=["Deadlock risk during handoff", "Race conditions in channel queues"],
                summary="High concurrency complexity in CSP handoff."
            ),
            SectionRiskAssessment(
                section_id="sec:system_config.md#静的定数",
                file_path="components/tier1_core/system_config.md",
                heading="静的定数",
                tier=1,
                complexity_score=1,
                risk_score=1,
                formal_needed=False,
                recommended_verification="Static",
                suggested_tags=[],
                risk_factors=[],
                summary="Simple declarative constants."
            )
        ],
        total_evaluated=2,
        formal_candidates_count=1,
        llm_candidates_count=0
    )

    md = report.to_markdown()
    assert "# Fireball 設計複雑度 & リスク評価レポート" in md
    assert "形式検証 (pyModelChecking) 推奨セクション" in md
    assert "`components/tier1_core/os_coos.md`" in md
    assert "`{VERIFY_FORMAL}`" in md
