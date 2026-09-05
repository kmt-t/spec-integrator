from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class FormalBackingAmbiguousCheck(AntiSabotageCheck):
    """裏付けモデルの曖昧化: 同一 formal ディレクトリを複数設計書が参照し BACKS 指定がない問題を検出する。"""

    rule_code = "FORMAL-BACKING-AMBIGUOUS"
    name = "裏付けモデルの曖昧化"
    gate = "Formal"
    severity = "ERROR"
    description = "複数の設計書が同一の検証モデル群に依存しているにもかかわらず BACKS 属性で対象を明示していない曖昧性を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        if ctx.formal_results:
            # formal_results の中で BACKING_AMBIGUOUS な結果または詳細を確認
            for r in ctx.formal_results:
                if r.status == "BACKING_AMBIGUOUS":
                    for doc_path in r.backing_documents:
                        issues.append(
                            VerificationIssue(
                                gate=self.gate,
                                severity=self.severity,
                                file_path=doc_path,
                                line=1,
                                rule_code=self.rule_code,
                                message=(
                                    f"Formal backing ambiguous for '{r.model_file}': {r.details}"
                                ),
                            )
                        )
        return issues
