from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.graph import Graph
from spec_integrator.models import ParsedDocument, VerificationIssue

__all__ = ["StaticVerifier", "VerificationIssue", "levenshtein_distance"]


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculates the Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


class StaticVerifier:
    def __init__(self, config: Config):
        self.config = config

    def verify(
        self, documents: list[ParsedDocument], graph: Graph, docs_root: Path
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        # 1. Format Gate (Links & Anchors)
        issues.extend(self._verify_format_gate(documents, graph, docs_root))
        # 2. Traceability Gate (Undefined / Unreferenced Keywords)
        issues.extend(self._verify_traceability_gate(documents, graph))
        # 3. Hierarchy Gate (Tier Boundary / Reverse Dependencies)
        issues.extend(self._verify_hierarchy_gate(documents, graph))
        return issues

    def _verify_format_gate(
        self, documents: list[ParsedDocument], graph: Graph, docs_root: Path
    ) -> list[VerificationIssue]:
        issues = []
        doc_map = {d.file_path: d for d in documents}
        for doc in documents:
            for link in doc.all_links:
                # 1. Target file check
                if not link.target_path:
                    target_file = doc.file_path
                else:
                    src_dir = Path(doc.file_path).parent
                    resolved_target = (src_dir / link.target_path).as_posix()
                    import os

                    target_file = os.path.normpath(resolved_target).replace("\\", "/")

                if target_file not in doc_map:
                    issues.append(
                        VerificationIssue(
                            gate="Format",
                            severity="ERROR",
                            file_path=doc.file_path,
                            line=link.source_line,
                            rule_code="FMT-BROKEN-LINK",
                            message=f"Broken Markdown link: '{link.target_path}' does not exist.",
                        )
                    )
                    continue
                # 2. Anchor check
                if link.target_anchor:
                    target_doc = doc_map[target_file]
                    # Check if anchor matches any heading
                    anchor_normalized = self._normalize_anchor(link.target_anchor)
                    found = False
                    for sec in target_doc.sections:
                        if (
                            self._normalize_anchor(sec.heading) == anchor_normalized
                            or sec.heading == link.target_anchor
                        ):
                            found = True
                            break
                    if not found:
                        issues.append(
                            VerificationIssue(
                                gate="Format",
                                severity="ERROR",
                                file_path=doc.file_path,
                                line=link.source_line,
                                rule_code="FMT-BROKEN-ANCHOR",
                                message=f"Broken anchor: '#{link.target_anchor}' not found in '{target_file}'.",
                            )
                        )

            # 3. Mermaid diagram syntax check
            issues.extend(self._verify_mermaid_blocks(doc))

        # 4. Levenshtein distance typo & variance check (Format Gate)
        issues.extend(self._verify_levenshtein_typos(documents))
        return issues

    def _verify_levenshtein_typos(self, documents: list[ParsedDocument]) -> list[VerificationIssue]:
        """Format Gate: Checks for potential typos or term variations using Levenshtein distance."""
        issues: list[VerificationIssue] = []
        stopwords = set(getattr(self.config.terminology, "stopwords", None) or [])

        katakana_occs: dict[str, list[tuple[str, int]]] = defaultdict(list)
        english_occs: dict[str, list[tuple[str, int]]] = defaultdict(list)

        katakana_pat = re.compile(r"[\u30A1-\u30FA\u30FC]{3,}")
        english_pat = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{4,}\b")

        for doc in documents:
            for sec in doc.sections:
                cleaned = re.sub(r"```[\s\S]*?```", " ", sec.body_text)
                cleaned = re.sub(r"`[^`\n]+`", " ", cleaned)
                # Katakana words (length >= 3)
                for m in katakana_pat.finditer(cleaned):
                    w = m.group(0)
                    if w not in stopwords:
                        katakana_occs[w].append((doc.file_path, sec.line_start))
                # English words (length >= 5)
                for m in english_pat.finditer(cleaned):
                    w = m.group(0)
                    lower = w.lower()
                    if lower not in stopwords and not w.isupper():
                        english_occs[w].append((doc.file_path, sec.line_start))

        # 1. Katakana Levenshtein checks
        reported_pairs: set[tuple[str, str]] = set()
        kata_vocab = sorted(katakana_occs.keys())
        kata_issues: list[VerificationIssue] = []

        for i in range(len(kata_vocab)):
            w1 = kata_vocab[i]
            len1 = len(w1)
            for j in range(i + 1, len(kata_vocab)):
                w2 = kata_vocab[j]
                len2 = len(w2)
                if abs(len1 - len2) > 2:
                    continue

                dist = levenshtein_distance(w1, w2)
                is_typo = (dist == 1) or (
                    dist == 2
                    and len1 >= 5
                    and len2 >= 5
                    and ("ー" in w1 or "ー" in w2 or "イ" in w1 or "イ" in w2)
                )

                if is_typo:
                    pair_key = (min(w1, w2), max(w1, w2))
                    if pair_key in reported_pairs:
                        continue
                    reported_pairs.add(pair_key)

                    occs1 = katakana_occs[w1]
                    occs2 = katakana_occs[w2]
                    target_occ = occs1[0] if len(occs1) <= len(occs2) else occs2[0]
                    other_occ = occs2[0] if len(occs1) <= len(occs2) else occs1[0]
                    target_word = w1 if len(occs1) <= len(occs2) else w2
                    other_word = w2 if len(occs1) <= len(occs2) else w1

                    kata_issues.append(
                        VerificationIssue(
                            gate="Format",
                            severity="WARNING",
                            file_path=target_occ[0],
                            line=target_occ[1],
                            rule_code="FMT-LEVENSHTEIN-TYPO",
                            message=f"Possible typo or spelling variance (Levenshtein distance={dist}): "
                            f"'{target_word}' vs '{other_word}' ({other_occ[0]}:{other_occ[1]}).",
                        )
                    )
                    if len(kata_issues) >= 20:
                        break
            if len(kata_issues) >= 20:
                break
        issues.extend(kata_issues)

        # 2. English Levenshtein checks (ignoring case differences)
        eng_vocab = sorted(english_occs.keys(), key=lambda x: x.lower())
        eng_issues: list[VerificationIssue] = []

        for i in range(len(eng_vocab)):
            w1 = eng_vocab[i]
            low1 = w1.lower()
            for j in range(i + 1, len(eng_vocab)):
                w2 = eng_vocab[j]
                low2 = w2.lower()
                if low1 == low2:
                    continue
                if abs(len(low1) - len(low2)) > 1:
                    continue
                if re.sub(r"\d+$", "", low1) == re.sub(r"\d+$", "", low2):
                    continue

                dist = levenshtein_distance(low1, low2)
                if dist == 1:
                    pair_key = (min(low1, low2), max(low1, low2))
                    if pair_key in reported_pairs:
                        continue
                    reported_pairs.add(pair_key)

                    occs1 = english_occs[w1]
                    occs2 = english_occs[w2]
                    target_occ = occs1[0] if len(occs1) <= len(occs2) else occs2[0]
                    other_occ = occs2[0] if len(occs1) <= len(occs2) else occs1[0]
                    target_word = w1 if len(occs1) <= len(occs2) else w2
                    other_word = w2 if len(occs1) <= len(occs2) else w1

                    eng_issues.append(
                        VerificationIssue(
                            gate="Format",
                            severity="WARNING",
                            file_path=target_occ[0],
                            line=target_occ[1],
                            rule_code="FMT-LEVENSHTEIN-TYPO",
                            message=f"Possible typo or spelling variance (Levenshtein distance={dist}): "
                            f"'{target_word}' vs '{other_word}' ({other_occ[0]}:{other_occ[1]}).",
                        )
                    )
                    if len(eng_issues) >= 20:
                        break
            if len(eng_issues) >= 20:
                break
        issues.extend(eng_issues)

        return issues

    def _verify_mermaid_blocks(self, doc: ParsedDocument) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        lines = doc.content.splitlines()
        in_mermaid = False
        start_line = 0
        buf = []
        for idx, line in enumerate(lines, start=1):
            if line.strip().startswith("```mermaid"):
                in_mermaid = True
                start_line = idx
                buf = []
            elif in_mermaid and line.strip().startswith("```"):
                in_mermaid = False
                issues.extend(self._check_mermaid_syntax(doc.file_path, start_line, buf))
            elif in_mermaid:
                buf.append((idx, line))
        return issues

    def _check_mermaid_syntax(
        self, file_path: str, start_line: int, lines_with_no: list[tuple[int, str]]
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        non_empty = [
            (lno, text.strip())
            for lno, text in lines_with_no
            if text.strip() and not text.strip().startswith("%%")
        ]
        if not non_empty:
            return [
                VerificationIssue(
                    gate="Format",
                    severity="WARNING",
                    file_path=file_path,
                    line=start_line,
                    rule_code="FMT-EMPTY-MERMAID",
                    message="Empty Mermaid diagram block.",
                )
            ]

        diagram_code = "\n".join(text for _, text in lines_with_no)
        # Delegate parsing and validation directly to the mermaidx library.
        # A missing/broken mermaidx must fail the gate, not silently skip
        # every diagram: a diagram we cannot parse is a diagram we cannot
        # vouch for, which is exactly the "sabotaged verification" failure
        # mode this pipeline exists to catch.
        try:
            import mermaidx
        except ImportError as e:
            issues.append(
                VerificationIssue(
                    gate="Format",
                    severity="ERROR",
                    file_path=file_path,
                    line=start_line,
                    rule_code="FMT-MERMAID-VALIDATOR-UNAVAILABLE",
                    message=f"mermaidx is not importable, so this Mermaid block cannot be "
                    f"validated: {e}. Install mermaidx (see pyproject.toml) rather "
                    f"than letting diagram syntax go unchecked.",
                )
            )
            return issues
        try:
            diag = mermaidx.Diagram(diagram_code)
            _ = diag.svg()
        except Exception as e:
            err_msg = str(e).strip()
            err_line = start_line
            m = re.search(r"line\s+(\d+)", err_msg, re.IGNORECASE)
            if m:
                err_line = start_line + int(m.group(1))

            first_err_line = err_msg.splitlines()[0] if err_msg.splitlines() else err_msg
            issues.append(
                VerificationIssue(
                    gate="Format",
                    severity="ERROR",
                    file_path=file_path,
                    line=err_line,
                    rule_code="FMT-INVALID-MERMAID",
                    message=f"Mermaid syntax error (mermaidx): {first_err_line}",
                )
            )
        return issues

    def _verify_traceability_gate(
        self, documents: list[ParsedDocument], graph: Graph
    ) -> list[VerificationIssue]:
        issues = []
        # Collect defined keywords
        defined_keywords: dict[str, str] = {}  # kw -> file_path
        defined_in_tier0: set[str] = set()
        for doc in documents:
            is_t0 = doc.tier == 0
            for kw in doc.all_keywords:
                if self._is_keyword_definition(kw, doc.file_path):
                    defined_keywords[kw] = doc.file_path
                    if is_t0:
                        defined_in_tier0.add(kw)

        # Check references: Are they defined?
        referenced_keywords: set[str] = set()
        for doc in documents:
            for sec in doc.sections:
                for kw in sec.keywords:
                    is_def = self._is_keyword_definition(kw, doc.file_path)
                    if is_def:
                        continue
                    # It's a reference
                    referenced_keywords.add(kw)
                    if kw not in defined_keywords:
                        # Undefined keyword reference
                        issues.append(
                            VerificationIssue(
                                gate="Traceability",
                                severity="ERROR",
                                file_path=doc.file_path,
                                line=sec.line_start,
                                rule_code="TRACE-UNDEFINED-KEYWORD",
                                message=f"Undefined keyword referenced: '{{{kw}}}'. No definition found in designated source of truth.",
                            )
                        )

        # Check Tier 0 requirements: Are they referenced in lower tiers?
        for kw in defined_in_tier0:
            if kw not in referenced_keywords:
                def_file = defined_keywords.get(kw, "Tier 0")
                issues.append(
                    VerificationIssue(
                        gate="Traceability",
                        severity="ERROR",
                        file_path=def_file,
                        line=1,
                        rule_code="TRACE-UNREFERENCED-REQUIREMENT",
                        message=f"Requirement '{{{kw}}}' is defined in Tier 0 but never referenced or refined in downstream component specs.",
                    )
                )
        return issues

    def _verify_hierarchy_gate(
        self, documents: list[ParsedDocument], graph: Graph
    ) -> list[VerificationIssue]:
        issues = []
        {d.file_path: d for d in documents}
        # Check all references & links
        for edge in graph.edges:
            src_node = graph.nodes.get(edge.source)
            tgt_node = graph.nodes.get(edge.target)
            if not src_node or not tgt_node:
                continue
            src_tier = src_node.tier
            if src_tier is None or src_tier == "meta":
                continue
            # Case A: Markdown link to lower tier
            if edge.relation == "links_to" and tgt_node.type in ("file", "section"):
                tgt_tier = tgt_node.tier
                if (
                    tgt_tier is not None
                    and tgt_tier != "meta"
                    and isinstance(src_tier, int)
                    and isinstance(tgt_tier, int)
                ):
                    if src_tier < tgt_tier:
                        issues.append(
                            VerificationIssue(
                                gate="Hierarchy",
                                severity="ERROR",
                                file_path=src_node.file_path,
                                line=src_node.line,
                                rule_code="HIERARCHY-REVERSE-DEPENDENCY",
                                message=f"Encapsulation violation: Upper Tier {src_tier} directly links to Lower Tier {tgt_tier} ('{tgt_node.label}').",
                            )
                        )

            # Case B: Keyword reference to lower tier local requirement
            elif edge.relation == "refers_to" and tgt_node.type == "item":
                kw_name = tgt_node.label.strip("{}")
                if kw_name.startswith("META_") or kw_name.startswith("GLOBAL_"):
                    # Meta and Global are exempt from hierarchy direction
                    continue
                # Find definition tier of this keyword
                def_tier = self._get_keyword_definition_tier(kw_name, documents)
                if (
                    def_tier is not None
                    and def_tier != "meta"
                    and isinstance(src_tier, int)
                    and isinstance(def_tier, int)
                ):
                    if src_tier < def_tier:
                        issues.append(
                            VerificationIssue(
                                gate="Hierarchy",
                                severity="ERROR",
                                file_path=src_node.file_path,
                                line=src_node.line,
                                rule_code="HIERARCHY-REVERSE-KEYWORD-REF",
                                message=f"Encapsulation violation: Upper Tier {src_tier} references Lower Tier {def_tier} keyword '{{{kw_name}}}'.",
                            )
                        )
        return issues

    def _is_keyword_definition(self, keyword: str, file_path: str) -> bool:
        return self.config.is_keyword_definition(keyword, file_path)

    def _get_keyword_definition_tier(
        self, keyword: str, documents: list[ParsedDocument]
    ) -> int | str | None:
        for doc in documents:
            if self._is_keyword_definition(keyword, doc.file_path):
                return doc.tier
        return None

    @staticmethod
    def _normalize_anchor(text: str) -> str:
        # Convert header to GitHub-compatible anchor id
        s = text.lower().strip()
        s = re.sub(r"[^\w\s\-]", "", s)
        s = re.sub(r"\s+", "-", s)
        return s
