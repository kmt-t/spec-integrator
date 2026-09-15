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
        global_defs: dict[str, list[tuple[str, int]]] = {}

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
                global_defs.setdefault(kw, []).append((doc.file_path, line_no))

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
                            f"Duplicate keyword definition for '{{{kw}}}' found in '{doc.file_path}' (lines {where}). "
                            "Reason: One keyword must have exactly one definition source of truth. With duplicate definitions, "
                            "spec updates become inconsistent and ambiguous. "
                            "Check the rules in 'docs/architecture/document_structure.md' and 'docs/architecture/keyword_dictionary.md'."
                        ),
                    )
                )

        # Cross-file duplicate definition check (excluding intra-file duplicates already reported)
        # Note: 'keyword_dictionary.md' is the registry for all keywords.
        # Duplication between a primary source-of-truth and the registry is expected.
        # A true cross-file duplicate occurs when 2 or more non-registry files define the same keyword.
        for kw, occurrences in sorted(global_defs.items()):
            files = list(dict.fromkeys(f for f, _ in occurrences))
            non_registry_occurrences = [item for item in occurrences if not item[0].endswith("keyword_dictionary.md")]
            non_registry_files = list(dict.fromkeys(f for f, _ in non_registry_occurrences))
            if len(non_registry_files) < 2:
                continue
            first_file, first_line = non_registry_occurrences[0]
            second_file, second_line = non_registry_occurrences[1]
            where = ", ".join(f"'{f}:{line}'" for f, line in occurrences)
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=second_file,
                    line=second_line,
                    rule_code=self.rule_code,
                    message=(
                        f"Duplicate keyword definition for '{{{kw}}}' found across multiple files ({where}). "
                        "Reason: One keyword must have exactly one definition source of truth. With duplicate definitions, "
                        "spec updates become inconsistent and ambiguous. "
                        "Check the rules in 'docs/architecture/document_structure.md' and 'docs/architecture/keyword_dictionary.md'."
                    ),
                )
            )

        return issues
