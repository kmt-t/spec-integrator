from __future__ import annotations

import os
from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class BrokenLinkCheck(AntiSabotageCheck):
    """リンク先の欠落: Markdown の相対リンクが指すファイルが実在するか検証する。"""

    rule_code = "FMT-BROKEN-LINK"
    name = "リンク先の欠落"
    gate = "Format"
    severity = "ERROR"
    description = "Markdown 相対リンクの指す対象ファイルが存在しない問題を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        doc_map = ctx.doc_map
        for doc in ctx.documents:
            for link in doc.all_links:
                if not link.target_path:
                    continue  # 同一ファイル内アンカーリンク
                src_dir = Path(doc.file_path).parent
                resolved_target = (src_dir / link.target_path).as_posix()
                target_file = os.path.normpath(resolved_target).replace("\\", "/")

                if target_file not in doc_map:
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=doc.file_path,
                            line=link.source_line,
                            rule_code=self.rule_code,
                            message=f"Broken Markdown link: '{link.target_path}' does not exist.",
                        )
                    )
        return issues
