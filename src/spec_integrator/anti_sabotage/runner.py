from __future__ import annotations

from typing import Sequence

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.anti_sabotage.checks import ALL_CHECKS
from spec_integrator.models import VerificationIssue


class AntiSabotageRunner:
    """Anti-Sabotage プラグインの統合実行エンジン。"""

    def __init__(
        self,
        checks: Sequence[AntiSabotageCheck | type[AntiSabotageCheck]] | None = None,
    ):
        raw_checks = checks if checks is not None else ALL_CHECKS
        self.checks: list[AntiSabotageCheck] = []
        for c in raw_checks:
            if isinstance(c, type) and issubclass(c, AntiSabotageCheck):
                self.checks.append(c())
            elif isinstance(c, AntiSabotageCheck):
                self.checks.append(c)

    def run(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        """登録されたすべての有効な Anti-Sabotage チェックを実行する。"""
        issues: list[VerificationIssue] = []
        for check in self.checks:
            if check.is_enabled(ctx):
                issues.extend(check.check(ctx))
        return issues

    def run_gate(self, gate: str, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        """特定のゲートに属するチェックのみを絞り込んで実行する。"""
        issues: list[VerificationIssue] = []
        target_gate = gate.lower()
        for check in self.checks:
            if check.gate.lower() == target_gate and check.is_enabled(ctx):
                issues.extend(check.check(ctx))
        return issues

    def get_check_by_code(self, rule_code: str) -> AntiSabotageCheck | None:
        for check in self.checks:
            if check.rule_code == rule_code:
                return check
        return None
