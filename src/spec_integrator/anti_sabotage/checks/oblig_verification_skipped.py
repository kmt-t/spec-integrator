from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class VerificationTagSkippedCheck(AntiSabotageCheck):
    """検証タグの欠落: risk_score が閾値以上なのに対応する {VERIFY_LLM} タグが付与されていない問題を検出する。"""

    rule_code = "OBLIG-VERIFICATION-SKIPPED"
    name = "検証タグの欠落"
    gate = "Obligation"
    severity = "ERROR"
    description = "高リスクと判定されたキーワード群が検証タグなしで放置されているサボりを検出する。"

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.obligation.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        db = ctx.db
        if db is None:
            return issues

        assessments = db.get_risk_assessments()
        if not assessments:
            return issues

        cfg = ctx.config.obligation
        llm_tag = ctx.config.llm_judge.tag
        doc_map = ctx.doc_map
        summary = ctx.extra.get("summary")

        for a in assessments:
            keyword = a.get("keyword", "")
            file_path = a.get("file_path", "")
            risk = int(a.get("risk_score", 0) or 0)
            covered_files = list(a.get("covered_files") or ([file_path] if file_path else []))
            line = int(a.get("line", 1) or 1)

            if risk < cfg.risk_threshold or not covered_files:
                continue

            if summary is not None:
                summary.demanded += 1

            present = any(llm_tag in doc_map[f].all_tags for f in covered_files if f in doc_map)
            if present:
                if summary is not None:
                    summary.discharged += 1
                continue

            if summary is not None:
                summary.skipped.append(
                    {
                        "keyword": keyword,
                        "file_path": file_path,
                        "risk_score": risk,
                        "missing_tags": [llm_tag],
                    }
                )

            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=file_path or (covered_files[0] if covered_files else "unknown"),
                    line=line,
                    rule_code=self.rule_code,
                    message=(
                        f"'{{{keyword}}}' was assessed at risk {risk}/5 and requires {llm_tag}, "
                        "but none of its defining/referencing documents carry that tag, so the "
                        "verification is never executed. Add the tag and supply the evidence, "
                        "or record an explicit, justified waiver."
                    ),
                )
            )

        return issues
