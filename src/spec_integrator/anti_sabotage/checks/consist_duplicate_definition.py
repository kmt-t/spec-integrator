from __future__ import annotations

import re

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class DuplicateDefinitionCheck(AntiSabotageCheck):
    """キーワード定義の重複: 同一キーワードが要求仕様テーブルの複数行で定義されている問題を検出する。"""

    rule_code = "CONSIST-DUPLICATE-DEFINITION"
    name = "キーワード定義の重複"
    gate = "Consistency"
    severity = "ERROR"
    description = "同一キーワードの定義が複数箇所に分散し、正本（Source of Truth）が曖昧化する問題を検出する。"

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.consistency.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for doc in ctx.documents:
            rows: dict[str, list[int]] = {}
            for line_no, line in enumerate(doc.content.splitlines(), start=1):
                m = re.match(r"^\s*\|\s*`?\{([A-Za-z0-9_\-]+)\}`?\s*\|", line)
                if not m:
                    continue
                kw = m.group(1)
                if not ctx.config.is_keyword_definition(kw, doc.file_path):
                    continue
                rows.setdefault(kw, []).append(line_no)

            for kw, lines in sorted(rows.items()):
                if len(lines) < 2:
                    continue
                where = ", ".join(str(n) for n in lines)
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=lines[1],
                        rule_code=self.rule_code,
                        message=(
                            f"'{{{kw}}}' is defined on more than one row of this table "
                            f"(lines {where}). One keyword must have one definition: with two, "
                            "an edit reaches whichever row the author happened to find and the "
                            "other silently keeps the old wording. Merge them into one row."
                        ),
                    )
                )
        return issues
