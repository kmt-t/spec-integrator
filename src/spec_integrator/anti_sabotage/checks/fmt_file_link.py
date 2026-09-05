# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class FileLinkFormatCheck(AntiSabotageCheck):
    """ファイルリンク形式規約:
    ドキュメント内のパスが『ファイル名を表示テキストとし、プロジェクトルートからの相対パスをリンク先とするリンク』
    になっているか検証する。
    """

    rule_code = "FMT-FILE-LINK-FORMAT"
    name = "ファイルリンク形式規約"
    gate = "Format"
    severity = "ERROR"
    description = (
        "ドキュメント中のパスが『ファイル名（basename）を表示テキストとし、"
        "プロジェクトルートからの相対パスをリンク先とするリンク』になっているか検証する。"
    )

    # Patterns matching file paths from repository root
    REPO_PATH_PATTERN = re.compile(
        r"(?<![\[\(/])\b((?:docs|inc|src|tests|tools|experiments)/[a-zA-Z0-9_\-\./]+\.[a-zA-Z0-9_]+)\b"
    )

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        repo_root = ctx.config.config_dir
        doc_map = ctx.doc_map

        # 1. Validate Markdown Links
        for doc in ctx.documents:
            for link in doc.all_links:
                if not link.target_path:
                    continue  # Same file anchor link

                target = link.target_path.strip()
                if (
                    target.startswith("http://")
                    or target.startswith("https://")
                    or target.startswith("mailto:")
                ):
                    continue

                # Check for absolute URLs/paths
                if (
                    target.startswith("file:")
                    or target.startswith("/")
                    or target.startswith("\\")
                    or re.match(r"^[a-zA-Z]:", target)
                ):
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=doc.file_path,
                            line=link.source_line,
                            rule_code=self.rule_code,
                            message=(
                                f"Absolute file link '{target}' forbidden. "
                                "Use a relative path from the project root."
                            ),
                        )
                    )
                    continue

                # Check for document-relative '../'
                if target.startswith("../") or target.startswith("..\\") or "/../" in target:
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=doc.file_path,
                            line=link.source_line,
                            rule_code=self.rule_code,
                            message=(
                                f"Document-relative link '{target}' forbidden. "
                                "Use a relative path from the project root (e.g. 'docs/...', 'inc/...')."
                            ),
                        )
                    )
                    continue

                norm_target = Path(target).as_posix()
                src_doc_dir = (ctx.docs_root / doc.file_path).parent
                target_exists = (
                    (repo_root / norm_target).exists()
                    or norm_target in doc_map
                    or (norm_target.startswith("docs/") and norm_target[5:] in doc_map)
                    or (ctx.docs_root / (norm_target[5:] if norm_target.startswith("docs/") else norm_target)).exists()
                )
                if not target_exists:
                    # Check if it would resolve relative to the document
                    if (src_doc_dir / target).exists():
                        rel_to_repo = Path(os.path.relpath(src_doc_dir / target, repo_root)).as_posix()
                        issues.append(
                            VerificationIssue(
                                gate=self.gate,
                                severity=self.severity,
                                file_path=doc.file_path,
                                line=link.source_line,
                                rule_code=self.rule_code,
                                message=(
                                    f"Document-local link '{target}' must be specified from the project root "
                                    f"(e.g. '{rel_to_repo}')."
                                ),
                            )
                        )
                        continue

                # Check if link text is the filename (basename)
                expected_basename = Path(norm_target).name
                clean_text = link.text.strip().strip("`")
                # Allow basename with optional section/anchor/line suffix (e.g. foo.md#section or foo.py:L10)
                if expected_basename and not (
                    clean_text == expected_basename
                    or clean_text.startswith(expected_basename + "#")
                    or clean_text.startswith(expected_basename + ":")
                ):
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=doc.file_path,
                            line=link.source_line,
                            rule_code=self.rule_code,
                            message=(
                                f"Link text '{link.text}' must be the filename '{expected_basename}' "
                                f"for target '{target}'."
                            ),
                        )
                    )

        # 2. Check for Unlinked File Paths in Prose
        for doc in ctx.documents:
            lines = doc.content.splitlines()
            in_code_block = False
            for line_idx, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if in_code_block:
                    continue

                # Ignore comments and frontmatter
                if stripped.startswith("<!--") or stripped.startswith("---"):
                    continue

                # Mask out markdown links: [text](target) -> replace with spaces
                masked_line = re.sub(r"\[[^\]]*\]\([^)]*\)", lambda m: " " * len(m.group(0)), line)
                # Mask out HTML links if any: <a href="..."> -> spaces
                masked_line = re.sub(r"<a\s+[^>]*>", lambda m: " " * len(m.group(0)), masked_line)

                for m in self.REPO_PATH_PATTERN.finditer(masked_line):
                    candidate_path = m.group(1).rstrip(".,:;)")
                    norm_cand = Path(candidate_path).as_posix()
                    # Check if candidate file actually exists
                    exists = False
                    if (repo_root / norm_cand).exists():
                        exists = True
                    elif norm_cand in doc_map:
                        exists = True
                    elif norm_cand.startswith("docs/") and norm_cand[5:] in doc_map:
                        exists = True
                    elif (ctx.docs_root / (norm_cand[5:] if norm_cand.startswith("docs/") else norm_cand)).exists():
                        exists = True

                    if exists:
                        basename = Path(norm_cand).name
                        issues.append(
                            VerificationIssue(
                                gate=self.gate,
                                severity=self.severity,
                                file_path=doc.file_path,
                                line=line_idx,
                                rule_code=self.rule_code,
                                message=(
                                    f"Unlinked file path '{candidate_path}' detected. "
                                    f"Format as Markdown link with filename: '[{basename}]({norm_cand})'."
                                ),
                            )
                        )

        return issues
