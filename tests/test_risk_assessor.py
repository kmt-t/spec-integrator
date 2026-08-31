from spec_integrator.judge.risk_assessor import KeywordRiskAssessment, RiskAssessmentReport


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
