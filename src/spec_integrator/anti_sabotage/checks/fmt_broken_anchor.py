from __future__ import annotations

import os
import re
from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


def normalize_anchor(text: str) -> str:
    """ヘッダー文字列を GitHub 互換のアンカー ID に正規化する。"""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s\-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


class BrokenAnchorCheck(AntiSabotageCheck):
    """見出しアンカーの欠落: リンク先の見出しアンカーが対象ドキュメント内に実在するか検証する。"""

    rule_code = "FMT-BROKEN-ANCHOR"
    name = "見出しアンカーの欠落"
    gate = "Format"
    severity = "ERROR"
    description = "Markdown 相対リンクの見出しアンカーが実在しない問題を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        doc_map = ctx.doc_map
        for doc in ctx.documents:
            for link in doc.all_links:
                if not link.target_anchor:
                    continue

                if not link.target_path:
                    target_file = doc.file_path
                else:
                    norm_root = os.path.normpath(link.target_path).replace("\\", "/")
                    if norm_root in doc_map:
                        target_file = norm_root
                    else:
                        src_dir = Path(doc.file_path).parent
                        resolved_target = (src_dir / link.target_path).as_posix()
                        target_file = os.path.normpath(resolved_target).replace("\\", "/")

                if target_file not in doc_map:
                    continue  # target file 欠落は BrokenLinkCheck 側で検出

                target_doc = doc_map[target_file]
                anchor_normalized = normalize_anchor(link.target_anchor)
                found = any(
                    normalize_anchor(sec.heading) == anchor_normalized
                    or sec.heading == link.target_anchor
                    for sec in target_doc.sections
                )
                if not found:
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=doc.file_path,
                            line=link.source_line,
                            rule_code=self.rule_code,
                            message=f"Broken anchor: '#{link.target_anchor}' not found in '{target_file}'.",
                        )
                    )
        return issues
