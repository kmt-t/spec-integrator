from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class DuplicateDefinitionCheck(AntiSabotageCheck):
    """キーワード定義宣言の重複を検出する。"""

    rule_code = "CONSIST-DUPLICATE-DEFINITION"
    name = "キーワード定義の重複"
    gate = "Consistency"
    severity = "ERROR"
    description = "同一キーワードの定義が複数箇所に分散し、正本（Source of Truth）が曖昧化する問題を検出する。"

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.consistency.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        global_defs: dict[str, list[tuple[str, int]]] = {}

        for doc in ctx.documents:
            for section in doc.sections:
                for keyword in section.canonical_definition_keywords:
                    global_defs.setdefault(keyword, []).append((doc.file_path, section.line_start))

        for kw, occurrences in sorted(global_defs.items()):
            if len(occurrences) < 2:
                continue
            _, second_line = occurrences[1]
            second_file = occurrences[1][0]
            where = ", ".join(f"'{file}:{line}'" for file, line in occurrences)
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=second_file,
                    line=second_line,
                    rule_code=self.rule_code,
                    message=(
                        f"Duplicate keyword definition for '{{{kw}}}' found at multiple declarations ({where}). "
                        "Reason: One keyword must have exactly one definition source of truth. With duplicate definitions, "
                        "spec updates become inconsistent and ambiguous. "
                        "Check the keyword definition rules in 'docs/architecture/document_structure.md'."
                    ),
                )
            )

        return issues
