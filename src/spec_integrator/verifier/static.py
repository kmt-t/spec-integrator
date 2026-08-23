from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument
from spec_integrator.graph import Graph


@dataclass
class VerificationIssue:
    gate: str          # "Format", "Traceability", "Hierarchy", "Formal"
    severity: str      # "ERROR" or "WARNING"
    file_path: str
    line: int
    rule_code: str
    message: str


class StaticVerifier:
    def __init__(self, config: Config):
        self.config = config

    def verify(self, documents: list[ParsedDocument], graph: Graph, docs_root: Path) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []

        # 1. Format Gate (Links & Anchors)
        issues.extend(self._verify_format_gate(documents, graph, docs_root))

        # 2. Traceability Gate (Undefined / Unreferenced Keywords)
        issues.extend(self._verify_traceability_gate(documents, graph))

        # 3. Hierarchy Gate (Tier Boundary / Reverse Dependencies)
        issues.extend(self._verify_hierarchy_gate(documents, graph))

        return issues

    def _verify_format_gate(self, documents: list[ParsedDocument], graph: Graph, docs_root: Path) -> list[VerificationIssue]:
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
                    issues.append(VerificationIssue(
                        gate="Format",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=link.source_line,
                        rule_code="FMT-BROKEN-LINK",
                        message=f"Broken Markdown link: '{link.target_path}' does not exist."
                    ))
                    continue

                # 2. Anchor check
                if link.target_anchor:
                    target_doc = doc_map[target_file]
                    # Check if anchor matches any heading
                    anchor_normalized = self._normalize_anchor(link.target_anchor)
                    found = False
                    for sec in target_doc.sections:
                        if self._normalize_anchor(sec.heading) == anchor_normalized or sec.heading == link.target_anchor:
                            found = True
                            break
                    if not found:
                        issues.append(VerificationIssue(
                            gate="Format",
                            severity="ERROR",
                            file_path=doc.file_path,
                            line=link.source_line,
                            rule_code="FMT-BROKEN-ANCHOR",
                            message=f"Broken anchor: '#{link.target_anchor}' not found in '{target_file}'."
                        ))

            # 3. Mermaid diagram syntax check
            issues.extend(self._verify_mermaid_blocks(doc))

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

    def _check_mermaid_syntax(self, file_path: str, start_line: int, lines_with_no: list[tuple[int, str]]) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        non_empty = [(lno, text.strip()) for lno, text in lines_with_no if text.strip() and not text.strip().startswith("%%")]
        if not non_empty:
            return [VerificationIssue(
                gate="Format", severity="WARNING", file_path=file_path, line=start_line,
                rule_code="FMT-EMPTY-MERMAID", message="Empty Mermaid diagram block."
            )]

        header_line = non_empty[0][1]
        first_word = header_line.split()[0] if header_line.split() else ""

        # 1. Structural and Syntax Validation (Pure Python AST/Rules)
        if first_word in ("graph", "flowchart"):
            issues.extend(self._validate_mermaid_flowchart(file_path, start_line, lines_with_no))
        elif first_word == "sequenceDiagram":
            issues.extend(self._validate_mermaid_sequence(file_path, start_line, lines_with_no))
        elif first_word in ("stateDiagram", "stateDiagram-v2"):
            issues.extend(self._validate_mermaid_state(file_path, start_line, lines_with_no))

        # 2. Engine render verification (if mermaidx is available)
        diagram_code = "\n".join(text for _, text in lines_with_no)
        try:
            import mermaidx
            diag = mermaidx.Diagram(diagram_code)
            _ = diag.svg()
        except ImportError:
            pass
        except Exception as e:
            err_msg = str(e).strip()
            err_line = start_line
            m = re.search(r"line\s+(\d+)", err_msg, re.IGNORECASE)
            if m:
                err_line = start_line + int(m.group(1))

            first_err_line = err_msg.splitlines()[0] if err_msg.splitlines() else err_msg
            issues.append(VerificationIssue(
                gate="Format",
                severity="ERROR",
                file_path=file_path,
                line=err_line,
                rule_code="FMT-INVALID-MERMAID",
                message=f"Mermaid syntax error (mermaidx engine): {first_err_line}"
            ))

        return issues

    def _validate_mermaid_flowchart(self, file_path: str, start_line: int, lines_with_no: list[tuple[int, str]]) -> list[VerificationIssue]:
        issues = []
        subgraph_stack: list[int] = []

        for line_no, line in lines_with_no:
            s = line.strip()
            if not s or s.startswith("%%"):
                continue

            if s.startswith("subgraph"):
                subgraph_stack.append(line_no)
            elif s == "end" or s.startswith("end ") or s.startswith("end;"):
                if not subgraph_stack:
                    issues.append(VerificationIssue(
                        gate="Format", severity="ERROR", file_path=file_path, line=line_no,
                        rule_code="FMT-MERMAID-UNEXPECTED-END", message="Unexpected 'end' without matching 'subgraph'."
                    ))
                else:
                    subgraph_stack.pop()

            # Check for unquoted bracket node labels containing parentheses, braces, colons, or commas
            # e.g., Node[Label (with parens)] -> invalid unless Node["Label (with parens)"]
            for m in re.finditer(r'(\b[A-Za-z0-9_]+)\[([^"\]\n]*[(){}:,][^"\]\n]*)\]', s):
                node_id = m.group(1)
                inner = m.group(2)
                if not inner.startswith('"'):
                    issues.append(VerificationIssue(
                        gate="Format", severity="ERROR", file_path=file_path, line=line_no,
                        rule_code="FMT-MERMAID-UNQUOTED-LABEL",
                        message=f"Unquoted special characters in node label '{node_id}[{inner}]'. Wrap label in double quotes: '{node_id}[\"{inner}\"]'."
                    ))

            # Check for broken bracket nesting like A[Text [nested] more]
            if re.search(r'\[[^"\]\n]*\[[^"\]\n]*\]', s):
                issues.append(VerificationIssue(
                    gate="Format", severity="ERROR", file_path=file_path, line=line_no,
                    rule_code="FMT-MERMAID-NESTED-BRACKETS", message=f"Malformed or nested brackets in node definition: '{s}'."
                ))

        if subgraph_stack:
            issues.append(VerificationIssue(
                gate="Format", severity="ERROR", file_path=file_path, line=subgraph_stack[-1],
                rule_code="FMT-MERMAID-UNCLOSED-SUBGRAPH", message="Unclosed 'subgraph' (missing matching 'end')."
            ))

        return issues

    def _validate_mermaid_sequence(self, file_path: str, start_line: int, lines_with_no: list[tuple[int, str]]) -> list[VerificationIssue]:
        issues = []
        block_stack: list[tuple[str, int]] = []

        for line_no, line in lines_with_no:
            s = line.strip()
            if not s or s.startswith("%%") or s == "sequenceDiagram":
                continue

            first = s.split()[0] if s.split() else ""
            if first in ("alt", "opt", "loop", "par", "critical", "rect", "group"):
                block_stack.append((first, line_no))
            elif first == "else":
                if not block_stack or block_stack[-1][0] not in ("alt", "critical", "par"):
                    issues.append(VerificationIssue(
                        gate="Format", severity="ERROR", file_path=file_path, line=line_no,
                        rule_code="FMT-MERMAID-UNMATCHED-ELSE", message="'else' without matching 'alt' or 'critical' block."
                    ))
            elif first == "end" or s == "end" or s.startswith("end;"):
                if not block_stack:
                    issues.append(VerificationIssue(
                        gate="Format", severity="ERROR", file_path=file_path, line=line_no,
                        rule_code="FMT-MERMAID-UNEXPECTED-END", message="Unexpected 'end' without matching sequence block (opt/alt/loop/rect/par)."
                    ))
                else:
                    block_stack.pop()

        for blk, blk_line in block_stack:
            issues.append(VerificationIssue(
                gate="Format", severity="ERROR", file_path=file_path, line=blk_line,
                rule_code="FMT-MERMAID-UNCLOSED-BLOCK", message=f"Unclosed sequence block '{blk}' (missing matching 'end')."
            ))

        return issues

    def _validate_mermaid_state(self, file_path: str, start_line: int, lines_with_no: list[tuple[int, str]]) -> list[VerificationIssue]:
        issues = []
        state_stack: list[int] = []

        for line_no, line in lines_with_no:
            s = line.strip()
            if not s or s.startswith("%%") or s in ("stateDiagram", "stateDiagram-v2"):
                continue

            # Composite state: 'state StateName {'
            if s.startswith("state ") and s.endswith("{"):
                state_stack.append(line_no)
            elif s == "}":
                if not state_stack:
                    issues.append(VerificationIssue(
                        gate="Format", severity="ERROR", file_path=file_path, line=line_no,
                        rule_code="FMT-MERMAID-UNEXPECTED-BRACE", message="Unexpected '}' without matching composite state definition."
                    ))
                else:
                    state_stack.pop()

        if state_stack:
            issues.append(VerificationIssue(
                gate="Format", severity="ERROR", file_path=file_path, line=state_stack[-1],
                rule_code="FMT-MERMAID-UNCLOSED-STATE", message="Unclosed composite state definition (missing matching '}')."
            ))

        return issues

    def _verify_traceability_gate(self, documents: list[ParsedDocument], graph: Graph) -> list[VerificationIssue]:
        issues = []
        
        # Collect defined keywords
        defined_keywords: dict[str, str] = {}  # kw -> file_path
        defined_in_tier0: set[str] = set()

        for doc in documents:
            is_t0 = (doc.tier == 0)
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
                        issues.append(VerificationIssue(
                            gate="Traceability",
                            severity="ERROR",
                            file_path=doc.file_path,
                            line=sec.line_start,
                            rule_code="TRACE-UNDEFINED-KEYWORD",
                            message=f"Undefined keyword referenced: '{{{kw}}}'. No definition found in designated source of truth."
                        ))

        # Check Tier 0 requirements: Are they referenced in lower tiers?
        for kw in defined_in_tier0:
            if kw not in referenced_keywords:
                def_file = defined_keywords.get(kw, "Tier 0")
                issues.append(VerificationIssue(
                    gate="Traceability",
                    severity="ERROR",
                    file_path=def_file,
                    line=1,
                    rule_code="TRACE-UNREFERENCED-REQUIREMENT",
                    message=f"Requirement '{{{kw}}}' is defined in Tier 0 but never referenced or refined in downstream component specs."
                ))

        return issues

    def _verify_hierarchy_gate(self, documents: list[ParsedDocument], graph: Graph) -> list[VerificationIssue]:
        issues = []
        doc_map = {d.file_path: d for d in documents}

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
                if tgt_tier is not None and tgt_tier != "meta" and isinstance(src_tier, int) and isinstance(tgt_tier, int):
                    if src_tier < tgt_tier:
                        issues.append(VerificationIssue(
                            gate="Hierarchy",
                            severity="ERROR",
                            file_path=src_node.file_path,
                            line=src_node.line,
                            rule_code="HIERARCHY-REVERSE-DEPENDENCY",
                            message=f"Encapsulation violation: Upper Tier {src_tier} directly links to Lower Tier {tgt_tier} ('{tgt_node.label}')."
                        ))

            # Case B: Keyword reference to lower tier local requirement
            elif edge.relation == "refers_to" and tgt_node.type == "item":
                kw_name = tgt_node.label.strip("{}")
                if kw_name.startswith("META_") or kw_name.startswith("GLOBAL_"):
                    # Meta and Global are exempt from hierarchy direction
                    continue

                # Find definition tier of this keyword
                def_tier = self._get_keyword_definition_tier(kw_name, documents)
                if def_tier is not None and def_tier != "meta" and isinstance(src_tier, int) and isinstance(def_tier, int):
                    if src_tier < def_tier:
                        issues.append(VerificationIssue(
                            gate="Hierarchy",
                            severity="ERROR",
                            file_path=src_node.file_path,
                            line=src_node.line,
                            rule_code="HIERARCHY-REVERSE-KEYWORD-REF",
                            message=f"Encapsulation violation: Upper Tier {src_tier} references Lower Tier {def_tier} keyword '{{{kw_name}}}'."
                        ))

        return issues

    def _is_keyword_definition(self, keyword: str, file_path: str) -> bool:
        return self.config.is_keyword_definition(keyword, file_path)

    def _get_keyword_definition_tier(self, keyword: str, documents: list[ParsedDocument]) -> int | str | None:
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
