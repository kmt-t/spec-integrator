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
        repo_root = ctx.config.config_dir
        for doc in ctx.documents:
            for link in doc.all_links:
                if not link.target_path:
                    continue  # 同一ファイル内アンカーリンク

                target_raw = link.target_path
                if target_raw.startswith("file:///"):
                    # file:// absolute URL
                    file_path = target_raw[8:]
                    if Path(file_path).exists():
                        continue

                # 1. Project-root relative resolution
                norm_root = os.path.normpath(target_raw).replace("\\", "/")
                # 2. Document-relative resolution
                src_dir = Path(doc.file_path).parent
                norm_doc = os.path.normpath((src_dir / target_raw).as_posix()).replace("\\", "/")

                exists = False
                if norm_root in doc_map or (repo_root / norm_root).exists():
                    exists = True
                elif norm_doc in doc_map or (ctx.docs_root / norm_doc).exists() or (repo_root / norm_doc).exists():
                    exists = True

                if not exists:
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
