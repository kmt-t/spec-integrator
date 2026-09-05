from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class FormalPropertyVacuousCheck(AntiSabotageCheck):
    """検証命題の空虚化: 違反状態を満たす状態がモデル中に存在せず自明に真となっている空虚な命題を検出する。"""

    rule_code = "FORMAL-PROPERTY-VACUOUS"
    name = "検証命題の空虚化"
    gate = "Formal"
    severity = "ERROR"
    description = "違反状態が状態空間に現れず、構造上必ず真になってしまう空虚な証明を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        if ctx.formal_results:
            for r in ctx.formal_results:
                for p in r.properties:
                    if p.status == "VACUOUS":
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
                                    f"is vacuous: {p.details}"
                                ),
                            )
                        )
        return issues
