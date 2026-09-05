from __future__ import annotations

import re
from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import ParsedDocument, VerificationIssue

FENCE_RE = re.compile(r"^\s*(```|~~~)")
URL_RE = re.compile(r"https?://\S+")


class DanglingArtifactRefCheck(AntiSabotageCheck):
    """参照アーティファクトの欠落: 本文中で言及されたファイルパスが実在するか検証する。"""

    rule_code = "EVID-DANGLING-ARTIFACT-REF"
    name = "参照アーティファクトの欠落"
    gate = "Evidence"
    severity = "ERROR"
    description = (
        "本文中で参照されたモデル・レポート・設計書等のファイルが実在しない問題を検出する。"
    )

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.evidence.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        exts = "|".join(re.escape(e) for e in ctx.config.evidence.artifact_extensions)
        ref_re = re.compile(rf"(?<![\w/*<])((?:\.\./)*[\w][\w./-]*\.(?:{exts}))\b")
        ignore = set(ctx.config.evidence.ignore_artifact_refs)
        repo_root = ctx.config.config_dir

        for doc in ctx.documents:
            seen: set[tuple[str, int]] = set()
            for line_no, line, in_code in self._iter_prose_lines(doc):
                if in_code:
                    continue
                scrubbed = URL_RE.sub(" ", line)
                for m in ref_re.finditer(scrubbed):
                    ref = m.group(1)
                    if ref in ignore or ref.startswith("http"):
                        continue
                    if "*" in ref or "<" in ref or ">" in ref:
                        continue
                    if (ref, line_no) in seen:
                        continue
                    seen.add((ref, line_no))
                    # If this ref is part of a markdown link [ref](target), check if target resolves
                    link_m = re.search(rf"\[\s*`?{re.escape(ref)}`?\s*\]\(([^)#]+)", line)
                    if link_m:
                        link_target = link_m.group(1).strip()
                        if self._resolves(link_target, doc, ctx.docs_root, repo_root):
                            continue

                    if self._resolves(ref, doc, ctx.docs_root, repo_root):
                        continue
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=doc.file_path,
                            line=line_no,
                            rule_code=self.rule_code,
                            message=(
                                f"Referenced artifact '{ref}' does not exist. A specification must not "
                                "point at a model, report or document that was never created."
                            ),
                        )
                    )
        return issues

    def _resolves(self, ref: str, doc: ParsedDocument, docs_root: Path, repo_root: Path) -> bool:
        candidates = [
            doc.full_path.parent / ref,
            docs_root / ref,
            repo_root / ref,
        ]
        if ref.startswith("docs/"):
            candidates.append(repo_root / ref)
            candidates.append(docs_root / ref[len("docs/") :])
        for c in candidates:
            try:
                if c.exists():
                    return True
            except OSError:
                continue
        name = Path(ref).name
        if name == ref:
            try:
                if next(docs_root.rglob(name), None) is not None:
                    return True
                if next(repo_root.rglob(name), None) is not None:
                    return True
            except OSError:
                pass
        return False

    @staticmethod
    def _iter_prose_lines(doc: ParsedDocument):
        in_code = False
        for idx, line in enumerate(doc.content.splitlines(), start=1):
            if FENCE_RE.match(line):
                in_code = not in_code
                yield idx, line, True
                continue
            yield idx, line, in_code
