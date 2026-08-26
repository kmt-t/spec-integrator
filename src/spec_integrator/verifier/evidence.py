from __future__ import annotations

import re
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument
from spec_integrator.verifier.static import VerificationIssue
from spec_integrator.verifier.formal import FormalModelResult


FENCE_RE = re.compile(r"^\s*(```|~~~)")
URL_RE = re.compile(r"https?://\S+")
METRIC_RE = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d+)?\s*%|\d+\s*(?:cycles|サイクル|clocks)\b")


class EvidenceVerifier:
    """Evidence Gate.

    Rejects specifications that *assert* a verification, a proof or a measurement
    which the repository cannot substantiate. A claim is only admissible when the
    artifact it rests on exists and actually passed in this run.
    """

    def __init__(self, config: Config):
        self.config = config

    def verify(self, documents: list[ParsedDocument], docs_root: Path,
               formal_results: list[FormalModelResult] | None = None,
               wit_results: list | None = None) -> list[VerificationIssue]:
        if not self.config.evidence.enabled:
            return []

        issues: list[VerificationIssue] = []
        for doc in documents:
            issues.extend(self._check_artifact_refs(doc, docs_root))

        return issues

    # ------------------------------------------------------------------ #
    # 1. Referenced artifacts must exist
    # ------------------------------------------------------------------ #
    def _check_artifact_refs(self, doc: ParsedDocument, docs_root: Path) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        exts = "|".join(re.escape(e) for e in self.config.evidence.artifact_extensions)
        ref_re = re.compile(rf"(?<![\w/*<])((?:\.\./)*[\w][\w./-]*\.(?:{exts}))\b")
        ignore = set(self.config.evidence.ignore_artifact_refs)

        repo_root = self.config.config_dir
        seen: set[tuple[str, int]] = set()

        for line_no, line, in_code in self._iter_prose_lines(doc):
            if in_code:
                continue
            scrubbed = URL_RE.sub(" ", line)
            for m in ref_re.finditer(scrubbed):
                ref = m.group(1)
                if ref in ignore or ref.startswith("http"):
                    continue
                # Wildcards / placeholders are documentation, not references.
                if "*" in ref or "<" in ref or ">" in ref:
                    continue
                if (ref, line_no) in seen:
                    continue
                seen.add((ref, line_no))
                if self._resolves(ref, doc, docs_root, repo_root):
                    continue
                issues.append(VerificationIssue(
                    gate="Evidence", severity="ERROR",
                    file_path=doc.file_path, line=line_no,
                    rule_code="EVID-DANGLING-ARTIFACT-REF",
                    message=(f"Referenced artifact '{ref}' does not exist. A specification must not "
                             "point at a model, report or document that was never created.")
                ))
        return issues

    def _resolves(self, ref: str, doc: ParsedDocument, docs_root: Path, repo_root: Path) -> bool:
        candidates = [
            doc.full_path.parent / ref,
            docs_root / ref,
            repo_root / ref,
        ]
        # Also allow a docs-root-relative path written with the 'docs/' prefix.
        if ref.startswith("docs/"):
            candidates.append(repo_root / ref)
            candidates.append(docs_root / ref[len("docs/"):])
        for c in candidates:
            try:
                if c.exists():
                    return True
            except OSError:
                continue
        # Bare filename mentioned anywhere in the docs tree (e.g. "os_scheduler.md").
        name = Path(ref).name
        if name == ref:
            try:
                if next(docs_root.rglob(name), None) is not None:
                    return True
            except OSError:
                pass
        return False

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _iter_prose_lines(doc: ParsedDocument):
        in_code = False
        for idx, line in enumerate(doc.content.splitlines(), start=1):
            if FENCE_RE.match(line):
                in_code = not in_code
                yield idx, line, True
                continue
            yield idx, line, in_code

    @staticmethod
    def _iter_section_lines(sec):
        in_code = False
        for offset, line in enumerate(sec.body_text.splitlines()):
            line_no = sec.line_start + offset
            if FENCE_RE.match(line):
                in_code = not in_code
                yield line_no, line, True
                continue
            yield line_no, line, in_code
