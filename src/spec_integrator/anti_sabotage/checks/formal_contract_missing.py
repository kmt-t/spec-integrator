from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class FormalContractMissingCheck(AntiSabotageCheck):
    """監査契約の欠落: 形式モデルが build_model() または properties() を公開していない問題を検出する。"""

    rule_code = "FORMAL-MODEL-NO-CONTRACT"
    name = "監査契約の欠落"
    gate = "Formal"
    severity = "ERROR"
    description = "モデルの状態空間や検証命題を機械的に検査できない契約不備を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        if ctx.formal_results:
            for r in ctx.formal_results:
                if r.status == "NO_CONTRACT":
                    first_doc = r.backing_documents[0] if r.backing_documents else r.model_file
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=first_doc,
                            line=1,
                            rule_code=self.rule_code,
                            message=f"Formal model '{r.model_file}' violates audit contract: {r.details}",
                        )
                    )
        return issues
