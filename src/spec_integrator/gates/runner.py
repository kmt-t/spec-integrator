from __future__ import annotations

from typing import Sequence

from spec_integrator.gates.base import GateCheck, GateContext
from spec_integrator.models import VerificationIssue


class GateRunner:
    """Automated Gate プラグインの統合実行エンジン。"""

    def __init__(
        self,
        checks: Sequence[GateCheck | type[GateCheck]] | None = None,
    ):
        if checks is None:
            from spec_integrator.checks import ALL_CHECKS

            raw_checks = ALL_CHECKS
        else:
            raw_checks = checks
        self.checks: list[GateCheck] = []
        for c in raw_checks:
            if isinstance(c, type) and issubclass(c, GateCheck):
                self.checks.append(c())
            elif isinstance(c, GateCheck):
                self.checks.append(c)

    def run(self, ctx: GateContext) -> list[VerificationIssue]:
        """登録されたすべての有効な Automated Gate チェックを実行する。"""
        issues: list[VerificationIssue] = []
        for check in self.checks:
            if check.is_enabled(ctx):
                issues.extend(check.check(ctx))
        return issues

    def run_gate(self, gate: str, ctx: GateContext) -> list[VerificationIssue]:
        """特定のゲートに属するチェックのみを絞り込んで実行する。"""
        issues: list[VerificationIssue] = []
        target_gate = gate.lower()
        for check in self.checks:
            if check.gate.lower() == target_gate and check.is_enabled(ctx):
                issues.extend(check.check(ctx))
        return issues

    def get_check_by_code(self, rule_code: str) -> GateCheck | None:
        for check in self.checks:
            if check.rule_code == rule_code:
                return check
        return None
