from __future__ import annotations

import fnmatch
import re

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class StaleValueCheck(AntiSabotageCheck):
    """旧値の残存: 設定済みの禁止パターン（移行済みの旧値・旧語彙）が文書中に残っている問題を検出する。"""

    rule_code = "CONSIST-STALE-VALUE"
    name = "旧値の残存"
    gate = "Consistency"
    severity = "ERROR"
    description = "アーキテクチャ移行等に伴い禁止された旧仕様値・記述パターンの残存を検出する。"

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.consistency.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        cfg = ctx.config.consistency
        invariants = getattr(cfg, "invariants", []) or []
        if not invariants:
            return []

        targets: list[tuple[str, list[str]]] = [
            (doc.file_path, doc.content.splitlines()) for doc in ctx.documents
        ]

        issues: list[VerificationIssue] = []
        for inv in invariants:
            if "summary" in ctx.extra and hasattr(ctx.extra["summary"], "invariants_checked"):
                ctx.extra["summary"].invariants_checked += 1

            patterns = [(p, re.compile(p)) for p in inv.get("forbidden", [])]
            scope = inv.get("scope") or ["**/*"]
            exclude = inv.get("exclude") or []
            canonical = inv.get("canonical")
            tail = f" 正: {canonical}" if canonical else ""

            for rel_path, lines in targets:
                if not any(fnmatch.fnmatch(rel_path, s) for s in scope):
                    continue
                if any(fnmatch.fnmatch(rel_path, e) for e in exclude):
                    continue
                for idx, line in enumerate(lines, start=1):
                    for raw, rx in patterns:
                        if not rx.search(line):
                            continue
                        issues.append(
                            VerificationIssue(
                                gate=self.gate,
                                severity=self.severity,
                                file_path=rel_path,
                                line=idx,
                                rule_code=self.rule_code,
                                message=(
                                    f"[{inv.get('id', 'invariant')}] superseded value matching "
                                    f"`{raw}` still present.{tail} — {inv.get('reason', '')}"
                                ),
                            )
                        )
        return issues
