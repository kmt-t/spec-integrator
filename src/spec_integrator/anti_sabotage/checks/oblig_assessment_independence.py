from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class AssessmentIndependenceCheck(AntiSabotageCheck):
    """評価エンジンの独立性・カバレッジ: 禁止リスト登録バックエンドでの自己参照やカバレッジ不足を検出する。"""

    rule_code = "OBLIG-ASSESSMENT-NOT-INDEPENDENT"
    name = "評価エンジンの自己参照・カバレッジ不足"
    gate = "Obligation"
    severity = "ERROR"
    description = (
        "タグから機械的に逆算するモック評価や、未評価キーワードを残した部分監査を検出する。"
    )

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.obligation.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        if not ctx.config.obligation.require_assessment:
            return issues

        db = ctx.db
        if db is None:
            return issues

        run_meta = db.get_run_metadata("risk_assessment") or {}
        backend = str(run_meta.get("backend") or "").lower()
        forbidden = set(ctx.config.obligation.forbidden_backends)

        if backend and backend in forbidden:
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=str(ctx.config.get_db_path()),
                    line=1,
                    rule_code="OBLIG-ASSESSMENT-NOT-INDEPENDENT",
                    message=(
                        f"The risk assessment was produced by the '{backend}' backend, which "
                        "derives each obligation from the tags the document already carries. "
                        "The discharge rate is then true by construction and says nothing about "
                        "the specification. Re-run 'llm-assess' against a real backend."
                    ),
                )
            )

        assessments = db.get_risk_assessments()
        if not assessments:
            return issues

        summary = ctx.extra.get("summary")
        if summary is not None:
            summary.keywords_assessed = len(assessments)
            if ctx.graph is not None:
                summary.keywords_total = len(ctx.graph.extract_item_subgraphs())
                if (
                    ctx.config.obligation.require_full_coverage
                    and summary.keywords_assessed < summary.keywords_total
                ):
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=str(ctx.config.get_db_path()),
                            line=1,
                            rule_code="OBLIG-ASSESSMENT-PARTIAL",
                            message=(
                                f"Only {summary.keywords_assessed} of {summary.keywords_total} "
                                "keyword(s) were risk-assessed, so the verification obligations of "
                                "the remainder are unknown. A discharge rate computed over a partial "
                                "assessment does not mean the specification is covered. Re-run "
                                "'llm-assess --exhaustive'."
                            ),
                        )
                    )

        return issues
