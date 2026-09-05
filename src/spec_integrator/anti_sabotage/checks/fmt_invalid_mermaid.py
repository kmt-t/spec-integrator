from __future__ import annotations

import re

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class MermaidSyntaxCheck(AntiSabotageCheck):
    """Mermaid 構文のエラー / 検証エンジンの欠落: ダイアグラム構文を QuickJS / mermaidx で検証する。"""

    rule_code = "FMT-INVALID-MERMAID"
    name = "Mermaid 構文のエラー"
    gate = "Format"
    severity = "ERROR"
    description = "Mermaid 記法の構文不備やレンダリング破綻、検証ライブラリ欠落を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for doc in ctx.documents:
            issues.extend(self._verify_document_mermaid(doc))
        return issues

    def _verify_document_mermaid(self, doc) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        lines = doc.content.splitlines()
        in_mermaid = False
        start_line = 0
        buf: list[tuple[int, str]] = []

        for idx, line in enumerate(lines, start=1):
            if line.strip().startswith("```mermaid"):
                in_mermaid = True
                start_line = idx
                buf = []
                continue
            if in_mermaid:
                if line.strip().startswith("```"):
                    in_mermaid = False
                    issues.extend(self._validate_block(doc.file_path, start_line, buf))
                    buf = []
                else:
                    buf.append((idx, line))

        if in_mermaid and buf:
            issues.extend(self._validate_block(doc.file_path, start_line, buf))
        return issues

    def _validate_block(
        self, file_path: str, start_line: int, lines_with_no: list[tuple[int, str]]
    ) -> list[VerificationIssue]:
        non_empty = [
            (lno, text.strip())
            for lno, text in lines_with_no
            if text.strip() and not text.strip().startswith("%%")
        ]
        if not non_empty:
            return [
                VerificationIssue(
                    gate=self.gate,
                    severity="WARNING",
                    file_path=file_path,
                    line=start_line,
                    rule_code="FMT-EMPTY-MERMAID",
                    message="Empty Mermaid diagram block.",
                )
            ]

        diagram_code = "\n".join(text for _, text in lines_with_no)
        try:
            import mermaidx
        except ImportError as e:
            return [
                VerificationIssue(
                    gate=self.gate,
                    severity="ERROR",
                    file_path=file_path,
                    line=start_line,
                    rule_code="FMT-MERMAID-VALIDATOR-UNAVAILABLE",
                    message=(
                        f"mermaidx is not importable, so this Mermaid block cannot be "
                        f"validated: {e}. Install mermaidx rather than letting diagram "
                        "syntax go unchecked."
                    ),
                )
            ]

        try:
            diag = mermaidx.Diagram(diagram_code)
            _ = diag.svg()
        except Exception as e:
            err_msg = str(e).strip()
            err_line = start_line
            m = re.search(r"line\s+(\d+)", err_msg, re.IGNORECASE)
            if m:
                err_line = start_line + int(m.group(1))
            first_err = err_msg.splitlines()[0] if err_msg.splitlines() else err_msg
            return [
                VerificationIssue(
                    gate=self.gate,
                    severity="ERROR",
                    file_path=file_path,
                    line=err_line,
                    rule_code=self.rule_code,
                    message=f"Mermaid syntax error (mermaidx): {first_err}",
                )
            ]
        return []
