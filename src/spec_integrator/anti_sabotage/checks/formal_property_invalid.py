from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class FormalPropertyInvalidCheck(AntiSabotageCheck):
    """性質記述の不備: 原子命題の未出現や変異検査(guards=False)での未到達など、記述自体の欠陥を検出する。"""

    rule_code = "FORMAL-PROPERTY-INVALID"
    name = "性質記述の不備"
    gate = "Formal"
    severity = "ERROR"
    description = "変異検査（guards=False）で違反状態に到達できず保護機構の実効性を確認できない不備を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        if ctx.formal_results:
            for r in ctx.formal_results:
                for p in r.properties:
                    if p.status == "INVALID":
                        first_doc = r.backing_documents[0] if r.backing_documents else r.model_file
                        issues.append(
                            VerificationIssue(
                                gate=self.gate,
                                severity=self.severity,
                                file_path=first_doc,
                                line=1,
                                rule_code=self.rule_code,
                                message=(
                                    f"Property '{p.name}' in formal model '{r.model_file}' "
                                    f"is invalid: {p.details}"
                                ),
                            )
                        )
        return issues
