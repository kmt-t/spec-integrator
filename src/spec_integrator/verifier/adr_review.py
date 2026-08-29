from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.graph import Graph
from spec_integrator.parser import ParsedDocument


@dataclass
class ADRFinding:
    keyword: str
    is_bracket_keyword: bool  # True: `keyword` is a real `{ADR_*}` tag. False: a bare heading ID (e.g. "ADR-SCHED-001").
    file_path: str
    line: int
    reasons: list[str] = field(default_factory=list)
    option_count: int = 0
    referenced_file_count: int = 0
    in_working_diff: bool = False


@dataclass
class _ADRBlock:
    keyword: str          # display label: a `{ADR_*}` keyword, or a bare heading ID like "ADR-SCHED-001"
    bracket_keyword: str | None  # the `{ADR_*}` keyword if this block used one, else None
    line: int
    end_line: int
    text: str


class ADRReviewVerifier:
    """Flags ADR-style decisions that are worth a human second look.

    This is an advisory report, not a pass/fail gate (see `adr-review`
    subcommand) -- a single-option or component-local ADR is not automatically
    wrong, and a heuristic that auto-fails on these patterns would just
    replace one rubber stamp with another. It exists to shrink the pile a
    human has to eyeball, not to render the verdict:

    1. Isolated: the decision is never referenced by any OTHER component's
       design section -- it does not actually cross a component boundary,
       so calling it an architectural (cross-cutting) decision is suspect.
    2. Touched by the current uncommitted diff: worth checking whether the
       ADR text was edited to rationalize away a Judge/Gate complaint after
       the fact, rather than reflecting a decision made before the code/docs
       existed.
    3. Single-option or strawman: the "選択肢" (options) list has at most one
       real entry, or the non-chosen options are token-length compared to the
       chosen one -- i.e. there is no real trade-off discussion on the page.

    The corpus uses two different ADR authoring conventions, both handled:
      (a) `- **決定事項**: {ADR_Keyword}` inline bullet with 背景/選択肢/結論
          sub-bullets, tied to a `{ADR_*}` keyword in the DocGraph
          (e.g. jit_compiler.md).
      (b) `### ADR-<id>: <title>` heading with `- **決定事項**:` /
          `- **理由**:` bullets and no `{ADR_*}` keyword at all
          (e.g. os_scheduler.md). Cross-reference and options signals still
          apply; isolation is checked via plain-text search for the heading
          ID instead of the DocGraph, since no keyword node exists for it.
    """

    INLINE_DECISION_RE = re.compile(r"-\s*\*\*決定事項\*\*:\s*`?\{(ADR_[A-Za-z0-9_]+)\}`?")
    NEXT_INLINE_DECISION_RE = re.compile(r"\n-\s*\*\*決定事項\*\*:")
    HEADING_ADR_RE = re.compile(r"^(#{2,6})\s*(ADR[-_][A-Za-z0-9_\-]+)\s*[:：]", re.MULTILINE)
    NEXT_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)

    OPTION_HEADER_RE = re.compile(r"\*\*選択肢(?:と評価)?\*\*")
    OPTION_ENTRY_RE = re.compile(r"案\s*(\d+)\s*[:：]")
    CONCLUSION_RE = re.compile(r"\*\*結論\*\*[:：].*?案\s*(\d+)")
    NEXT_SUBHEADING_RE = re.compile(r"\n\s*-\s*\*\*[^\*]+\*\*[:：]")
    ADR_KEYWORD_RE = re.compile(r"\{(ADR_[A-Za-z0-9_]+)\}")

    STRAWMAN_MIN_RATIO = 0.35
    STRAWMAN_ABS_CHARS = 40

    def __init__(self, config: Config, repo_root: Path | None = None):
        self.config = config
        self.repo_root = repo_root or config.config_dir

    def count_blocks(self, documents: list[ParsedDocument]) -> int:
        return sum(len(self._find_blocks(d.content)) for d in documents)

    def verify(self, documents: list[ParsedDocument], graph: Graph) -> list[ADRFinding]:
        findings: list[ADRFinding] = []
        section_by_id = {s.section_id: (d, s) for d in documents for s in d.sections}
        subgraphs = {sg["item_label"].strip("{}"): sg for sg in graph.extract_item_subgraphs()}
        diff_lines_by_file = self._changed_lines_by_file()

        for doc in documents:
            for blk in self._find_blocks(doc.content):
                reasons: list[str] = []

                ref_files = self._referencing_files(blk, doc, documents, subgraphs, section_by_id)
                if not ref_files:
                    reasons.append(
                        "他コンポーネントの設計文書から一切参照されていない（孤立ADR。"
                        "本当に単一コンポーネント限定の決定か確認）"
                    )

                option_count, strawman_reason = self._evaluate_options(blk.text)
                if option_count <= 1:
                    reasons.append(f"「選択肢」の実質エントリ数が{option_count}件で、対抗案の比較検討が見当たらない")
                elif strawman_reason:
                    reasons.append(strawman_reason)

                touched_lines = diff_lines_by_file.get(doc.file_path, set())
                touched = any(blk.line <= ln <= blk.end_line for ln in touched_lines)
                if touched:
                    reasons.append(
                        "現在の未コミット差分でこの決定事項ブロックが追加/変更されている"
                        "（Judge/Gate失敗の帳尻合わせで後付けされた記述でないか確認）"
                    )

                if reasons:
                    findings.append(ADRFinding(
                        keyword=blk.keyword,
                        is_bracket_keyword=(blk.keyword == blk.bracket_keyword),
                        file_path=doc.file_path,
                        line=blk.line,
                        reasons=reasons,
                        option_count=option_count,
                        referenced_file_count=len(ref_files),
                        in_working_diff=touched,
                    ))

        return findings

    # ------------------------------------------------------------------ #
    def _find_blocks(self, content: str) -> list[_ADRBlock]:
        blocks: list[_ADRBlock] = []

        for m in self.INLINE_DECISION_RE.finditer(content):
            keyword = m.group(1)
            line = content.count("\n", 0, m.start()) + 1
            nm = self.NEXT_INLINE_DECISION_RE.search(content, m.end())
            end = nm.start() if nm else len(content)
            end_line = content.count("\n", 0, end) + 1
            blocks.append(_ADRBlock(keyword=keyword, bracket_keyword=keyword, line=line,
                                     end_line=end_line, text=content[m.end():end]))

        for m in self.HEADING_ADR_RE.finditer(content):
            heading_id = m.group(2)
            line = content.count("\n", 0, m.start()) + 1
            level = len(m.group(1))
            nm = self.NEXT_HEADING_RE.search(content, m.end())
            while nm and len(content[nm.start():].split(None, 1)[0]) > level:
                nm = self.NEXT_HEADING_RE.search(content, nm.end())
            end = nm.start() if nm else len(content)
            end_line = content.count("\n", 0, end) + 1
            block_text = content[m.end():end]
            # If the traceability comment right under the heading also carries a
            # real {ADR_*} keyword, prefer the DocGraph-backed cross-reference
            # check over the plain-text heading-ID search below.
            head_region = block_text.split("\n\n", 1)[0]
            bracket_m = self.ADR_KEYWORD_RE.search(head_region)
            blocks.append(_ADRBlock(
                keyword=heading_id,
                bracket_keyword=bracket_m.group(1) if bracket_m else None,
                line=line,
                end_line=end_line,
                text=block_text,
            ))

        return blocks

    def _referencing_files(self, blk: _ADRBlock, owning_doc: ParsedDocument,
                            documents: list[ParsedDocument], subgraphs: dict,
                            section_by_id: dict) -> set[str]:
        if blk.bracket_keyword:
            sg = subgraphs.get(blk.bracket_keyword)
            if not sg:
                return set()
            files: set[str] = set()
            for sec_id in sg.get("referenced_in", []):
                entry = section_by_id.get(sec_id)
                if entry:
                    files.add(entry[0].file_path)
            files.discard(owning_doc.file_path)
            return files

        # Heading-style ADR with no {ADR_*} keyword: fall back to a plain-text
        # search for the heading ID (e.g. "ADR-SCHED-002") in every other
        # document -- there is no DocGraph item node to consult.
        files = set()
        for other in documents:
            if other.file_path == owning_doc.file_path:
                continue
            if blk.keyword in other.content:
                files.add(other.file_path)
        return files

    def _evaluate_options(self, block: str) -> tuple[int, str | None]:
        header = self.OPTION_HEADER_RE.search(block)
        scope = block[header.end():] if header else block

        entries: list[tuple[str, int, int]] = []  # (option_no, start, end)
        matches = list(self.OPTION_ENTRY_RE.finditer(scope))
        for i, om in enumerate(matches):
            entry_start = om.start()
            entry_end = matches[i + 1].start() if i + 1 < len(matches) else len(scope)
            # An option entry shouldn't bleed past the "選択肢" list into the
            # next labeled sub-bullet (**結論**, **評価**, etc.).
            next_sub = self.NEXT_SUBHEADING_RE.search(scope, entry_start + 1, entry_end)
            if next_sub:
                entry_end = min(entry_end, next_sub.start())
            entries.append((om.group(1), entry_start, entry_end))

        option_count = len(entries)
        if option_count <= 1:
            return option_count, None

        lengths = {no: len(scope[s:e].strip()) for no, s, e in entries}
        concl = self.CONCLUSION_RE.search(block)
        chosen_no = concl.group(1) if concl else None

        if chosen_no and chosen_no in lengths:
            chosen_len = lengths[chosen_no]
            others = {no: l for no, l in lengths.items() if no != chosen_no}
        else:
            chosen_len = max(lengths.values())
            others = {no: l for no, l in lengths.items() if l != chosen_len}

        if not others or chosen_len == 0:
            return option_count, None

        weak = [no for no, l in others.items()
                if l < self.STRAWMAN_ABS_CHARS or l < chosen_len * self.STRAWMAN_MIN_RATIO]
        if weak:
            return option_count, (
                f"非採用案（案{', '.join(sorted(weak))}）が採用案に比べて著しく短く記述されており、"
                "本気で比較検討された対抗案というより藁人形になっていないか確認"
            )
        return option_count, None

    def _changed_lines_by_file(self) -> dict[str, set[int]]:
        """Best-effort: maps doc file_path -> set of NEW-side line numbers touched
        by the current uncommitted working-tree diff (`git diff HEAD`). Returns an
        empty mapping (never raises) if git or a repo isn't available -- this
        signal is advisory, and its absence shouldn't break the other two."""
        result: dict[str, set[int]] = {}
        try:
            docs_rel = Path(self.config.project.docs_root).as_posix()
            proc = subprocess.run(
                ["git", "diff", "--unified=0", "--no-color", "HEAD", "--", docs_rel],
                cwd=self.repo_root, capture_output=True, timeout=30, check=False,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode not in (0, 1) or not proc.stdout:
                return result
        except Exception:
            return result

        current_file: str | None = None
        hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
        for raw_line in proc.stdout.splitlines():
            if raw_line.startswith("+++ "):
                path_str = raw_line[4:].strip()
                if path_str == "/dev/null":
                    current_file = None
                    continue
                path_str = path_str[2:] if path_str.startswith("b/") else path_str
                try:
                    rel = Path(path_str).relative_to(docs_rel).as_posix()
                except ValueError:
                    rel = None
                current_file = rel
                continue
            hm = hunk_re.match(raw_line)
            if hm and current_file:
                new_start = int(hm.group(1))
                new_count = int(hm.group(2)) if hm.group(2) is not None else 1
                lines = result.setdefault(current_file, set())
                for ln in range(new_start, new_start + max(new_count, 1)):
                    lines.add(ln)
        return result
