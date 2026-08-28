from __future__ import annotations

import re
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
import mistune
from spec_integrator.config import Config


@dataclass
class ParsedLink:
    source_file: str
    source_line: int
    text: str
    target_path: str
    target_anchor: str


@dataclass
class ParsedSection:
    section_id: str  # "sec:rel_path#Heading"
    file_path: str
    heading: str
    level: int
    line_start: int
    line_end: int
    body_text: str
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # e.g. {VERIFY_FORMAL}, {VERIFY_LLM}, {VERIFY_WIT}
    links: list[ParsedLink] = field(default_factory=list)


@dataclass
class ParsedDocument:
    file_path: str  # relative path POSIX style
    full_path: Path
    tier: int | str | None
    component: str
    content: str
    content_hash: str
    sections: list[ParsedSection] = field(default_factory=list)
    all_keywords: list[str] = field(default_factory=list)
    all_tags: list[str] = field(default_factory=list)
    all_links: list[ParsedLink] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)


class MarkdownParser:
    KEYWORD_REGEX = re.compile(r"\{([A-Za-z0-9_\-]+)\}")
    EVIDENCE_BLOCK_RE = re.compile(r"<!--\s*evidence:\s*(.*?)\s*-->", re.DOTALL | re.IGNORECASE)
    TEMPLATE_PREFIXES = ("Decision_", "Strategy_", "Requirement_", "req_", "concept", "Constraint_")

    def __init__(self, config: Config):
        self.config = config
        # Initialize mistune markdown AST parser
        self.md_parser = mistune.create_markdown(renderer=None)

    def _parse_evidence(self, content: str) -> dict[str, str]:
        evidence = {}
        for m in self.EVIDENCE_BLOCK_RE.finditer(content):
            block = m.group(1)
            for line in block.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip('"\'')
                    if k and v:
                        evidence[k] = v
                else:
                    for km in re.finditer(r'([a-zA-Z0-9_-]+)=["\']?([^"\'\s]+)["\']?', line):
                        evidence[km.group(1).lower()] = km.group(2)
        return evidence

    def parse_file(self, file_path: Path, docs_root: Path) -> ParsedDocument:
        rel_path = file_path.relative_to(docs_root).as_posix()
        content = file_path.read_text(encoding="utf-8")
        content_hash = self.config_compute_hash(content)
        tier = self.config.get_tier_for_path(rel_path)
        component = self._extract_component(rel_path)
        evidence = self._parse_evidence(content)

        lines = content.splitlines()
        
        # 1. Parse AST using mistune
        tokens = self.md_parser(content)
        
        # 2. Extract Headings and line boundaries from AST
        sections: list[ParsedSection] = []
        heading_indices = self._find_heading_lines(lines, tokens)

        if not heading_indices:
            sec = self._create_section(rel_path, "", 1, 1, max(len(lines), 1), lines)
            sections.append(sec)
        else:
            # Preamble section if content exists before first heading
            if heading_indices[0][0] > 1:
                pre_sec = self._create_section(
                    rel_path, "(Overview)", 1, 1, heading_indices[0][0] - 1,
                    lines[:heading_indices[0][0] - 1]
                )
                sections.append(pre_sec)

            for i, (line_start, level, heading) in enumerate(heading_indices):
                line_end = heading_indices[i + 1][0] - 1 if i + 1 < len(heading_indices) else len(lines)
                sec_lines = lines[line_start - 1:line_end]
                sec = self._create_section(rel_path, heading, level, line_start, line_end, sec_lines)
                sections.append(sec)

        all_keywords = []
        all_tags = []
        all_links = []
        for s in sections:
            all_keywords.extend(s.keywords)
            all_tags.extend(s.tags)
            all_links.extend(s.links)

        return ParsedDocument(
            file_path=rel_path,
            full_path=file_path,
            tier=tier,
            component=component,
            content=content,
            content_hash=content_hash,
            sections=sections,
            all_keywords=list(dict.fromkeys(all_keywords)),
            all_tags=list(dict.fromkeys(all_tags)),
            all_links=all_links,
            evidence=evidence
        )

    def _find_heading_lines(self, lines: list[str], tokens: list[dict]) -> list[tuple[int, int, str]]:
        """Extracts heading line numbers, levels, and text guided by mistune AST tokens."""
        ast_headings = []
        for tok in tokens:
            if tok.get("type") == "heading":
                level = tok.get("attrs", {}).get("level", 1)
                text = self._extract_text_from_children(tok.get("children", []))
                ast_headings.append((level, text.strip()))

        heading_indices = []
        line_idx = 0
        total_lines = len(lines)

        for level, heading_text in ast_headings:
            # Search forward in source lines for matching heading
            while line_idx < total_lines:
                line = lines[line_idx]
                h_match = re.match(r"^(#{1,6})\s+(.+)$", line)
                if h_match and len(h_match.group(1)) == level:
                    raw_title = h_match.group(2).strip()
                    # Match heading text (ignoring trailing tags/anchors if any)
                    heading_indices.append((line_idx + 1, level, raw_title))
                    line_idx += 1
                    break
                line_idx += 1

        return heading_indices

    def _extract_text_from_children(self, children: list[dict]) -> str:
        """Recursively extracts plain text from mistune AST token children."""
        res = []
        for child in children:
            if "raw" in child:
                res.append(child["raw"])
            elif "text" in child:
                res.append(child["text"])
            elif "children" in child:
                res.append(self._extract_text_from_children(child["children"]))
        return "".join(res)

    def _create_section(self, rel_path: str, heading: str, level: int,
                        line_start: int, line_end: int, lines: list[str]) -> ParsedSection:
        body_text = "\n".join(lines)
        section_id = f"sec:{rel_path}#{heading}" if heading else f"sec:{rel_path}"

        keywords = []
        tags = []
        links = []

        # Parse section body with mistune AST to reliably find links and inline elements
        sec_tokens = self.md_parser(body_text)
        extracted_links = self._extract_links_from_tokens(sec_tokens)

        # Line mapping for links and keywords
        in_code_block = False
        for line_offset, line in enumerate(lines):
            curr_line_num = line_start + line_offset
            stripped = line.strip()

            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue

            # Keywords and tags extraction (only outside code blocks)
            if not in_code_block:
                for m in self.KEYWORD_REGEX.finditer(line):
                    kw_val = m.group(1)
                    full_kw = f"{{{kw_val}}}"
                    if full_kw.startswith("{VERIFY_"):
                        tags.append(full_kw)
                    elif any(kw_val.startswith(p) for p in self.TEMPLATE_PREFIXES) or kw_val == "concept":
                        continue
                    else:
                        keywords.append(kw_val)

            # Match AST-extracted links with their line number in section
            for link_text, link_url in extracted_links:
                target_file, target_anchor = self._split_link_target(link_url)
                if (link_url in line) or (target_file and target_file in line) or (target_anchor and target_anchor in line) or (link_text and link_text in line):
                    parsed_link = ParsedLink(
                        source_file=rel_path,
                        source_line=curr_line_num,
                        text=link_text,
                        target_path=target_file,
                        target_anchor=target_anchor
                    )
                    if parsed_link not in links:
                        links.append(parsed_link)

        return ParsedSection(
            section_id=section_id,
            file_path=rel_path,
            heading=heading,
            level=level,
            line_start=line_start,
            line_end=line_end,
            body_text=body_text,
            keywords=keywords,
            tags=tags,
            links=links
        )

    def _extract_links_from_tokens(self, tokens: list[dict]) -> list[tuple[str, str]]:
        """Extracts (link_text, url) pairs from mistune AST tokens."""
        links = []

        def walk(toks):
            for t in toks:
                if t.get("type") == "link":
                    url = t.get("attrs", {}).get("url", "")
                    text = self._extract_text_from_children(t.get("children", []))
                    if url and (url.endswith(".md") or ".md#" in url or url.startswith("#")):
                        links.append((text, url))
                if "children" in t:
                    walk(t["children"])

        walk(tokens)
        return links

    @staticmethod
    def _split_link_target(url: str) -> tuple[str, str]:
        import urllib.parse
        decoded = urllib.parse.unquote(url)
        if "#" in decoded:
            file_part, anchor_part = decoded.split("#", 1)
            return file_part, anchor_part
        return decoded, ""

    @staticmethod
    def config_compute_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _extract_component(self, rel_path: str) -> str:
        parts = rel_path.split("/")
        if len(parts) >= 2 and parts[0] == "components":
            return parts[1]
        elif len(parts) >= 1:
            return parts[0]
        return "unknown"
