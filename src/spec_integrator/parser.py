from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
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
    section_id: str  # "rel_path#Heading"
    file_path: str
    heading: str
    level: int
    line_start: int
    line_end: int
    body_text: str
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # e.g. {VERIFY_FORMAL}, {VERIFY_LLM}
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


class MarkdownParser:
    KEYWORD_REGEX = re.compile(r"\{([A-Za-z0-9_\-]+)\}")
    LINK_REGEX = re.compile(r"\[([^\]]+)\]\(([^)#]+\.md)?(#[^)]+)?\)")

    def __init__(self, config: Config):
        self.config = config

    def parse_file(self, file_path: Path, docs_root: Path) -> ParsedDocument:
        rel_path = file_path.relative_to(docs_root).as_posix()
        content = file_path.read_text(encoding="utf-8")
        content_hash = self.config_compute_hash(content)
        tier = self.config.get_tier_for_path(rel_path)
        component = self._extract_component(rel_path)

        lines = content.splitlines()
        sections: list[ParsedSection] = []
        
        # Heading scan
        heading_indices = []
        for idx, line in enumerate(lines, start=1):
            h_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if h_match:
                level = len(h_match.group(1))
                raw_heading = h_match.group(2).strip()
                heading_indices.append((idx, level, raw_heading))

        if not heading_indices:
            # File without headings
            sec = self._create_section(rel_path, "", 1, 1, len(lines), lines)
            sections.append(sec)
        else:
            # If there's content before first heading
            if heading_indices[0][0] > 1:
                pre_sec = self._create_section(rel_path, "(Overview)", 1, 1, heading_indices[0][0] - 1, lines[:heading_indices[0][0] - 1])
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
            all_links=all_links
        )

    def _create_section(self, rel_path: str, heading: str, level: int,
                        line_start: int, line_end: int, lines: list[str]) -> ParsedSection:
        body_text = "\n".join(lines)
        section_id = f"sec:{rel_path}#{heading}" if heading else f"sec:{rel_path}"

        keywords = []
        tags = []
        links = []

        for line_offset, line in enumerate(lines):
            curr_line_num = line_start + line_offset
            
            # Keywords and tags
            for m in self.KEYWORD_REGEX.finditer(line):
                kw_val = m.group(1)
                full_kw = f"{{{kw_val}}}"
                if full_kw.startswith("{VERIFY_"):
                    tags.append(full_kw)
                else:
                    keywords.append(kw_val)

            # Markdown links
            for m in self.LINK_REGEX.finditer(line):
                link_text = m.group(1)
                target_file = m.group(2) or ""
                target_anchor = (m.group(3) or "").lstrip("#")
                links.append(ParsedLink(
                    source_file=rel_path,
                    source_line=curr_line_num,
                    text=link_text,
                    target_path=target_file,
                    target_anchor=target_anchor
                ))

        return ParsedSection(
            section_id=section_id,
            file_path=rel_path,
            heading=heading,
            level=level,
            line_start=line_start,
            line_end=line_end,
            body_text=body_text,
            keywords=list(dict.fromkeys(keywords)),
            tags=list(dict.fromkeys(tags)),
            links=links
        )

    def _extract_component(self, rel_path: str) -> str:
        parts = rel_path.replace("\\", "/").split("/")
        # If inside docs/components/tierX_name -> component is tierX_name
        for p in parts:
            if p.startswith("tier1_") or p.startswith("tier2_") or p.startswith("tier3_"):
                return p
        if len(parts) > 1:
            return parts[0]
        return "root"

    @staticmethod
    def config_compute_hash(content: str) -> str:
        import hashlib
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
