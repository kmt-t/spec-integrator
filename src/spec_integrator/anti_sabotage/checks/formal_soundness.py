from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class FormalModelSoundnessCheck(AntiSabotageCheck):
    """モデル構造の破綻: 到達不能状態の残存や分岐数<=1の単一経路など、モデルの構造的欠陥を検出する。"""

    rule_code = "FORMAL-MODEL-UNSOUND"
    name = "モデル構造の破綻"
    gate = "Formal"
    severity = "ERROR"
    description = (
        "到達不能な状態が残っている、または状態分岐数が1以下の無意味なモデル構造を検出する。"
    )

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        if ctx.formal_results:
            for r in ctx.formal_results:
                if r.status == "UNSOUND":
                    first_doc = r.backing_documents[0] if r.backing_documents else r.model_file
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=first_doc,
                            line=1,
                            rule_code=self.rule_code,
                            message=f"Formal model '{r.model_file}' state-space is unsound: {r.details}",
                        )
                    )
        return issues
