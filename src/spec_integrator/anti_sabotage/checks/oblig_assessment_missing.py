from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class AssessmentMissingCheck(AntiSabotageCheck):
    """リスク評価の未実施・記録欠落: llm-assess が未実施、またはバックエンドが記録されていない問題を検出する。"""

    rule_code = "OBLIG-ASSESSMENT-MISSING"
    name = "リスク評価の未実施・記録欠落"
    gate = "Obligation"
    severity = "ERROR"
    description = (
        "リスク評価が一度も実行されていない、または評価エンジンの出所が不明なサボりを検出する。"
    )

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.obligation.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        db = ctx.db
        cfg = ctx.config.obligation
        summary = ctx.extra.get("summary")

        if db is None:
            if cfg.require_assessment:
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=str(ctx.config.get_db_path()),
                        line=1,
                        rule_code="OBLIG-ASSESSMENT-MISSING",
                        message=(
                            "No cache DB available, so no risk assessment can be read. The "
                            "pipeline cannot claim the specification is verified without first "
                            "deciding what needs verifying. Run 'spec-integrator llm-assess' "
                            "before 'check'."
                        ),
                    )
                )
            return issues

        assessments = db.get_risk_assessments()
        doc_hashes = db.get_assessed_doc_hashes("risk_assessment")

        if not assessments and not doc_hashes:
            if cfg.require_assessment:
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=str(ctx.config.get_db_path()),
                        line=1,
                        rule_code="OBLIG-ASSESSMENT-MISSING",
                        message=(
                            "No risk assessment found in the cache DB. The pipeline cannot "
                            "claim the specification is verified without first deciding what "
                            "needs verifying. Run 'spec-integrator llm-assess' before 'check'."
                        ),
                    )
                )
            return issues

        run_meta = db.get_run_metadata("risk_assessment") or {}
        backend = str(run_meta.get("backend") or "").lower()
        if not backend:
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=str(ctx.config.get_db_path()),
                    line=1,
                    rule_code="OBLIG-ASSESSMENT-PROVENANCE-UNKNOWN",
                    message=(
                        "The risk assessment records no backend, so its independence from the "
                        "documents it judges cannot be established. Re-run 'llm-assess' with a "
                        "tool version that stamps the engine."
                    ),
                )
            )

        # サマリー集計
        if summary is not None:
            summary.keywords_assessed = len(assessments)
            assessed_files = {a["file_path"] for a in assessments if a.get("file_path")}
            summary.assessed_documents = len(assessed_files)
            for doc in ctx.documents:
                recorded = doc_hashes.get(doc.file_path)
                if recorded is None:
                    if doc.file_path in assessed_files:
                        continue
                    summary.unassessed_documents.append(doc.file_path)
                    continue
                if recorded != doc.content_hash:
                    summary.stale_documents.append(doc.file_path)
                    if cfg.stale_is_error:
                        issues.append(
                            VerificationIssue(
                                gate=self.gate,
                                severity=self.severity,
                                file_path=doc.file_path,
                                line=1,
                                rule_code="OBLIG-ASSESSMENT-STALE",
                                message=(
                                    "Document changed since it was risk-assessed. The recorded "
                                    "verification obligations no longer describe this content — "
                                    "re-run 'spec-integrator llm-assess'."
                                ),
                            )
                        )

        return issues
