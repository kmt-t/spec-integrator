from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from spec_integrator.config import Config
from spec_integrator.graph import Graph
from spec_integrator.parser import ParsedDocument


@dataclass
class DecisionFinding:
    keyword: str
    is_bracket_keyword: bool  # True if keyword is a formal DocGraph tag like {ADR_*}; False for prose/heading
    file_path: str
    line: int
    kind: str  # "PROSE_DECISION", "ADHOC_LOCAL_ADR", "LLM_FLAGGED"
    reasons: list[str] = field(default_factory=list)
    option_count: int = 0
    referenced_file_count: int = 0
    in_working_diff: bool = False
    confidence: str = "MEDIUM"  # "HIGH", "MEDIUM", "LOW"
    snippet: str = ""


@dataclass
class _LocalDecisionBlock:
    label: str
    bracket_keyword: str | None
    line: int
    end_line: int
    text: str


FAKE_DECISION_PROMPT = """You are a strict, skeptical Architecture & Specification Auditor.
Your mission is to scrutinize design specifications and uncommitted diffs to detect "Fake Decisions", "Ad-hoc Local ADRs", or "Unapproved/Unilateral Design Decisions Stated in Prose".

=== TARGET DOCUMENT / SECTION ===
File: {file_path}
Section: {section_title}

=== CONTENT UNDER REVIEW ===
{content_text}

=== EVALUATION CRITERIA ===
Scrutinize whether the author or agent made ad-hoc, unilateral, or fabricated decisions in the component text instead of adhering to authorized requirements:

1. Ad-hoc Prose Decisions (地の文での勝手な仕様決定):
   - Major architectural or component decisions (e.g. pinning physical registers, restricting memory layouts, inventing new error codes, imposing arbitrary size caps, altering calling conventions) stated as settled facts in normal prose without traceability to requirements or authorized system architecture.

2. Unilateral Papering-Over (辻褄合わせの独断決定):
   - Decisions fabricated to mask simulator/compiler mismatches, failed test runs, or spec inconsistencies without genuine cross-component trade-off analysis.

3. Ad-hoc Local ADRs (コンポーネント内の勝手なADR新設):
   - Local decision blocks created inside a leaf component that decide cross-cutting system policies unilaterally, especially those using single-option or strawman alternatives.

4. Unbacked Performance / Safety Claims:
   - Statements of "zero-cost", "always safe", "impossible to deadlock" asserted without citing verified formal models or benchmarks.

=== OUTPUT FORMAT ===
Respond ONLY with a valid JSON object in English:
```json
{{
  "is_problematic": true,
  "confidence": "HIGH",
  "issues": [
    {{
      "category": "PROSE_DECISION" | "PAPERING_OVER" | "ADHOC_LOCAL_ADR" | "UNBACKED_CLAIM",
      "summary": "Concise 1-sentence issue explanation in English",
      "reason": "Detailed explanation why this text represents an ad-hoc or unauthorized decision",
      "quote": "Short exact snippet from the text containing the ad-hoc decision"
    }}
  ]
}}
```
If no ad-hoc or fake decisions are found, respond with:
```json
{{
  "is_problematic": false,
  "confidence": "HIGH",
  "issues": []
}}
```
"""


class FakeDecisionDetector:
    """Detects ad-hoc, unilateral, or fabricated decisions in component prose and diffs.

    Focuses on checking whether an author/agent has unilaterally introduced
    ad-hoc ADRs or arbitrary decisions in the prose of a component, rather than
    adhering to authorized requirements:
    1. Prose Decisions: Heuristically scans prose for unilateral policy statements.
    2. Ad-hoc Local ADRs: Scans decision blocks for isolation, single-option, or strawman patterns.
    3. Working Diff Touches: Flags decisions added/altered in the active uncommitted diff.
    4. LLM Semantic Audit: Deep semantic verification of trade-off integrity and ad-hoc rules.
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

    # Patterns detecting unilateral decisions stated in prose
    PROSE_DECISION_PATTERNS = [
        re.compile(r"(?:設計方針|アーキテクチャ方針|基本方針|決定事項として|方針として|設計判断として)[：:]?\s*([^。\n]{15,120}[。])"),
        re.compile(r"(?:を採用する|に固定する|を禁止する|はサポート外とする|は不可とする|と決定した)[。]"),
    ]

    STRAWMAN_MIN_RATIO = 0.35
    STRAWMAN_ABS_CHARS = 40

    def __init__(self, config: Config, repo_root: Path | None = None):
        self.config = config
        self.repo_root = repo_root or config.config_dir

    def count_blocks(self, documents: list[ParsedDocument]) -> int:
        return sum(len(self._find_local_decision_blocks(d.content)) for d in documents)

    def verify(self, documents: list[ParsedDocument], graph: Graph) -> list[DecisionFinding]:
        """Perform static detection of ad-hoc decisions in prose and local blocks."""
        findings: list[DecisionFinding] = []
        section_by_id = {s.section_id: (d, s) for d in documents for s in d.sections}
        subgraphs = {sg["item_label"].strip("{}"): sg for sg in graph.extract_item_subgraphs()}
        diff_lines_by_file = self._changed_lines_by_file()

        # 1. Check local ADR blocks (detect ad-hoc local decisions disguised as ADRs)
        for doc in documents:
            for blk in self._find_local_decision_blocks(doc.content):
                reasons: list[str] = []

                ref_files = self._referencing_files(blk, doc, documents, subgraphs, section_by_id)
                if not ref_files:
                    reasons.append(
                        "他コンポーネントから一切参照されていない孤立した決定ブロック（コンポーネント内で勝手に閉じたアドホック決定の疑い）"
                    )

                option_count, strawman_reason = self._evaluate_options(blk.text)
                if option_count <= 1:
                    reasons.append(f"「選択肢」の実質エントリ数が{option_count}件で、対抗案のないアドホックな独断決定")
                elif strawman_reason:
                    reasons.append(strawman_reason)

                touched_lines = diff_lines_by_file.get(doc.file_path, set())
                touched = any(blk.line <= ln <= blk.end_line for ln in touched_lines)
                if touched:
                    reasons.append(
                        "現在の未コミット差分で決定ブロックが追加/変更されている（テストや辻褄合わせで後付けされた疑い）"
                    )

                if reasons:
                    findings.append(DecisionFinding(
                        keyword=blk.label,
                        is_bracket_keyword=(blk.label == blk.bracket_keyword),
                        file_path=doc.file_path,
                        line=blk.line,
                        kind="ADHOC_LOCAL_ADR",
                        reasons=reasons,
                        option_count=option_count,
                        referenced_file_count=len(ref_files),
                        in_working_diff=touched,
                        confidence="HIGH" if (touched and option_count <= 1) else "MEDIUM",
                        snippet=blk.text[:200].strip(),
                    ))

        # 2. Check prose for ad-hoc policy and decision statements
        prose_findings = self._detect_prose_decisions(documents, diff_lines_by_file)
        findings.extend(prose_findings)

        return findings

    def verify_with_llm(
        self,
        documents: list[ParsedDocument],
        backend_name: str = "sakura",
        diff_only: bool = False,
    ) -> list[DecisionFinding]:
        """Perform semantic LLM audit to detect ad-hoc decisions in prose and diffs."""
        diff_lines_by_file = self._changed_lines_by_file()
        findings: list[DecisionFinding] = []

        for doc in documents:
            touched_lines = diff_lines_by_file.get(doc.file_path, set())
            if diff_only and not touched_lines:
                continue

            for sec in doc.sections:
                sec_lines = set(range(sec.line_start, sec.line_end + 1))
                is_touched = bool(touched_lines & sec_lines)
                if diff_only and not is_touched:
                    continue

                has_decision_clue = any(
                    kw in sec.body_text for kw in ["ADR", "決定", "選択肢", "方針", "採用", "トレードオフ", "固定", "禁止"]
                )
                if not (is_touched or has_decision_clue):
                    continue

                prompt = FAKE_DECISION_PROMPT.format(
                    file_path=doc.file_path,
                    section_title=sec.heading,
                    content_text=sec.body_text[:4000],
                )

                try:
                    raw_resp = self._call_llm_backend(prompt, backend_name)
                    parsed = self._parse_llm_json(raw_resp)
                    if parsed.get("is_problematic"):
                        reasons = [
                            f"[{iss.get('category', 'ISSUE')}] {iss.get('summary', '')}: {iss.get('reason', '')}"
                            for iss in parsed.get("issues", [])
                        ]
                        if reasons:
                            findings.append(DecisionFinding(
                                keyword=sec.heading,
                                is_bracket_keyword=False,
                                file_path=doc.file_path,
                                line=sec.line_start,
                                kind="LLM_FLAGGED",
                                reasons=reasons,
                                in_working_diff=is_touched,
                                confidence=parsed.get("confidence", "MEDIUM"),
                                snippet=sec.body_text[:200].strip(),
                            ))
                except Exception as e:
                    print(f"[Warning] LLM fake decision audit failed for {doc.file_path}#{sec.heading}: {e}")

        return findings

    def _call_llm_backend(self, prompt: str, backend_name: str) -> str:
        from spec_integrator.judge.semantic_judge import SemanticJudge
        judge = SemanticJudge(self.config, backend_name=backend_name)
        backend_cfg = getattr(self.config.llm_judge, "backends", {}).get(backend_name)
        model = getattr(backend_cfg, "model", "sakura-ai-model") if backend_cfg else "sakura-ai-model"
        if backend_name == "sakura":
            return judge._call_sakura(prompt, model)
        elif backend_name == "openrouter":
            return judge._call_openrouter(prompt, model)
        else:
            return judge._call_ollama(prompt, model)

    # ------------------------------------------------------------------ #
    def _detect_prose_decisions(
        self, documents: list[ParsedDocument], diff_lines_by_file: dict[str, set[int]]
    ) -> list[DecisionFinding]:
        findings: list[DecisionFinding] = []

        for doc in documents:
            lines = doc.content.splitlines()
            touched_lines = diff_lines_by_file.get(doc.file_path, set())

            for line_idx, line in enumerate(lines, start=1):
                if line.startswith("```") or len(line.strip()) < 10:
                    continue

                if "決定事項" in line and ("ADR_" in line or "案1" in line):
                    continue

                for pat in self.PROSE_DECISION_PATTERNS:
                    m = pat.search(line)
                    if m:
                        matched_text = m.group(0)
                        is_touched = line_idx in touched_lines

                        if is_touched and ("固定する" in matched_text or "方針として" in matched_text or "禁止する" in matched_text or "と決定した" in matched_text):
                            findings.append(DecisionFinding(
                                keyword=f"Prose: L{line_idx}",
                                is_bracket_keyword=False,
                                file_path=doc.file_path,
                                line=line_idx,
                                kind="PROSE_DECISION",
                                reasons=[
                                    "コンポーネント内の地の文で、要求仕様や正規ADRの裏付けなく勝手な設計方針・制約が導入されている"
                                    "（アドホックな独断や辻褄合わせでないか確認）"
                                ],
                                in_working_diff=is_touched,
                                confidence="LOW",
                                snippet=line.strip()[:150],
                            ))
                            break

        return findings

    def _find_local_decision_blocks(self, content: str) -> list[_LocalDecisionBlock]:
        blocks: list[_LocalDecisionBlock] = []

        for m in self.INLINE_DECISION_RE.finditer(content):
            keyword = m.group(1)
            line = content.count("\n", 0, m.start()) + 1
            nm = self.NEXT_INLINE_DECISION_RE.search(content, m.end())
            end = nm.start() if nm else len(content)
            end_line = content.count("\n", 0, end) + 1
            blocks.append(_LocalDecisionBlock(label=keyword, bracket_keyword=keyword, line=line,
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
            head_region = block_text.split("\n\n", 1)[0]
            bracket_m = self.ADR_KEYWORD_RE.search(head_region)
            blocks.append(_LocalDecisionBlock(
                label=heading_id,
                bracket_keyword=bracket_m.group(1) if bracket_m else None,
                line=line,
                end_line=end_line,
                text=block_text,
            ))

        return blocks

    def _referencing_files(
        self, blk: _LocalDecisionBlock, owning_doc: ParsedDocument,
        documents: list[ParsedDocument], subgraphs: dict,
        section_by_id: dict
    ) -> set[str]:
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

        files = set()
        for other in documents:
            if other.file_path == owning_doc.file_path:
                continue
            if blk.label in other.content:
                files.add(other.file_path)
        return files

    def _evaluate_options(self, block: str) -> tuple[int, str | None]:
        header = self.OPTION_HEADER_RE.search(block)
        scope = block[header.end():] if header else block

        entries: list[tuple[str, int, int]] = []
        matches = list(self.OPTION_ENTRY_RE.finditer(scope))
        for i, om in enumerate(matches):
            entry_start = om.start()
            entry_end = matches[i + 1].start() if i + 1 < len(matches) else len(scope)
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

    def _parse_llm_json(self, raw_resp: str) -> dict[str, Any]:
        match = re.search(r"```json\s*(.*?)\s*```", raw_resp, re.DOTALL)
        clean_text = match.group(1) if match else raw_resp.strip()
        return json.loads(clean_text)

