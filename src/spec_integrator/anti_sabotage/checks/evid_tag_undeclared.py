from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class TagToEvidenceMismatchCheck(AntiSabotageCheck):
    """検証証跡の不一致: 検証タグを宣言しているのに evidence ブロックにエントリがない不一致を検出する。"""

    rule_code = "EVID-TAG-UNDECLARED"
    name = "検証証跡の不一致"
    gate = "Evidence"
    severity = "ERROR"
    description = "{VERIFY_FORMAL}, {VERIFY_WIT}, {VERIFY_BENCHMARK} タグに対応する証跡エントリの欠落を検出する。"

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.evidence.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for doc in ctx.documents:
            if "{VERIFY_FORMAL}" in doc.all_tags and "formal" not in doc.evidence:
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=1,
                        rule_code="EVID-FORMAL-UNDECLARED",
                        message=(
                            "Document declares '{VERIFY_FORMAL}' but carries no 'formal:' "
                            "entry in its '<!-- evidence: ... -->' block."
                        ),
                    )
                )
            if "{VERIFY_WIT}" in doc.all_tags and "wit" not in doc.evidence:
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=1,
                        rule_code="EVID-WIT-UNDECLARED",
                        message=(
                            "Document declares '{VERIFY_WIT}' but carries no 'wit:' "
                            "entry in its '<!-- evidence: ... -->' block."
                        ),
                    )
                )
            if "{VERIFY_BENCHMARK}" in doc.all_tags and "benchmark" not in doc.evidence:
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=1,
                        rule_code="EVID-BENCHMARK-UNDECLARED",
                        message=(
                            "Document declares '{VERIFY_BENCHMARK}' but carries no 'benchmark:' "
                            "entry in its '<!-- evidence: ... -->' block."
                        ),
                    )
                )
        return issues
