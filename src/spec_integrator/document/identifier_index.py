"""Full-source identifier indexing for the identifier quality gate."""

from __future__ import annotations

import fnmatch
import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Pattern

from spec_integrator.config import Config
from spec_integrator.models import IdentifierDefinition, IdentifierReference


@dataclass
class IdentifierScan:
    definitions: list[IdentifierDefinition]
    references: list[IdentifierReference]


@dataclass(frozen=True)
class _DefinitionSpan:
    file_path: str
    line: int
    start: int
    end: int
    family: str
    identifier: str
    definition_kind: str
    section_id: str | None


def scan_identifiers(config: Config) -> IdentifierScan:
    """Scan all configured identifier sources, independent of partial doc selection."""
    settings = config.identifiers
    assert settings.enabled
    assert settings.patterns
    assert settings.candidate_pattern
    candidate_pattern = re.compile(settings.candidate_pattern)
    family_patterns = {family: re.compile(pattern) for family, pattern in settings.patterns.items()}
    assert set(family_patterns) == {"TEST", "GOTCHA", "BENCHMARK", "FORMAL", "ADR"}

    source_files = _source_files(config)
    contents: dict[str, str] = {}
    hashes: dict[str, str] = {}
    section_maps: dict[str, list[tuple[int, str]]] = {}
    definition_spans: list[_DefinitionSpan] = []
    definitions: list[IdentifierDefinition] = []

    for path in source_files:
        rel_path = path.relative_to(config.config_dir).as_posix()
        content = path.read_text(encoding="utf-8")
        contents[rel_path] = content
        hashes[rel_path] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        section_maps[rel_path] = _section_map(rel_path, content)
        spans = _extract_definition_spans(
            rel_path,
            content,
            settings,
            candidate_pattern,
            family_patterns,
            section_maps[rel_path],
        )
        for span in spans:
            definition_spans.append(span)
            definitions.append(
                IdentifierDefinition(
                    family=span.family,
                    identifier=span.identifier,
                    normalized_id=span.identifier,
                    definition_kind=span.definition_kind,
                    file_path=rel_path,
                    section_id=span.section_id,
                    line=span.line,
                    column_number=span.start + 1,
                    source_hash=hashes[rel_path],
                )
            )

    definition_keys = {
        (span.file_path, span.line, span.start, span.end) for span in definition_spans
    }
    references: list[IdentifierReference] = []
    for rel_path, content in contents.items():
        for line_number, line in enumerate(content.splitlines(), start=1):
            matches = list(candidate_pattern.finditer(line))
            index = 0
            while index < len(matches):
                current = matches[index]
                if _is_markdown_anchor(rel_path, line, current.start()):
                    index += 1
                    continue
                current_key = (rel_path, line_number, current.start(), current.end())
                if current_key in definition_keys:
                    index += 1
                    continue

                raw_text = current.group(0)
                range_end: str | None = None
                range_notation: str | None = None
                if index + 1 < len(matches):
                    following = matches[index + 1]
                    if line[current.end() : following.start()] == settings.range_notation:
                        range_end = following.group(0)
                        raw_text = line[current.start() : following.end()]
                        range_notation = settings.range_notation
                        index += 1

                start_id = current.group(0)
                family = start_id.split("-", maxsplit=1)[0].upper()
                valid_start = _valid_identifier(start_id, family_patterns)
                expanded = _expand_range(
                    start_id,
                    range_end,
                    definitions,
                    family_patterns,
                )
                references.append(
                    IdentifierReference(
                        family=family,
                        identifier=start_id,
                        raw_text=raw_text,
                        reference_kind=(
                            "range"
                            if range_end is not None
                            else _reference_kind(rel_path, valid_start)
                        ),
                        file_path=rel_path,
                        section_id=_section_for_line(rel_path, line_number, section_maps[rel_path]),
                        line=line_number,
                        column_number=current.start() + 1,
                        range_end=range_end,
                        range_notation=range_notation,
                        expanded_ids=expanded,
                    )
                )
                index += 1

    return IdentifierScan(definitions=definitions, references=references)


def next_identifier(config: Config, prefix: str) -> str:
    """Return max serial + 1 for a series prefix using a fresh source scan."""
    scan = scan_identifiers(config)
    normalized_prefix = prefix.upper()
    family, separator, component = normalized_prefix.partition("-")
    assert separator and component
    assert family in config.identifiers.patterns

    serials: list[tuple[int, int]] = []
    patterns = {name: re.compile(pattern) for name, pattern in config.identifiers.patterns.items()}
    identifiers = [item.identifier for item in scan.definitions]
    for reference in scan.references:
        identifiers.append(reference.identifier)
        if reference.range_end is not None:
            identifiers.append(reference.range_end)
    for identifier in identifiers:
        if not identifier.upper().startswith(f"{normalized_prefix}-"):
            continue
        match = patterns[family].fullmatch(identifier)
        if match is None:
            continue
        serial_text = match.groupdict().get("serial")
        assert serial_text is not None
        serials.append((int(serial_text), len(serial_text)))

    assert serials, f"No identifiers found for prefix: {prefix}"
    maximum = max(serial for serial, _ in serials)
    width = max(2, max(width for serial, width in serials if serial == maximum))
    return f"{normalized_prefix}-{maximum + 1:0{width}d}"


def _source_files(config: Config) -> list[Path]:
    paths: set[Path] = set()
    for pattern in config.identifiers.scan_globs:
        paths.update(path.resolve() for path in config.config_dir.glob(pattern) if path.is_file())
    docs_root = config.get_docs_dir()
    return [
        path
        for path in sorted(paths)
        if not config.is_excluded(path, docs_root if path.is_relative_to(docs_root) else None)
    ]


def _extract_definition_spans(
    rel_path: str,
    content: str,
    settings,
    candidate_pattern: Pattern[str],
    family_patterns: dict[str, Pattern[str]],
    section_map: list[tuple[int, str]],
) -> list[_DefinitionSpan]:
    spans: list[_DefinitionSpan] = []
    test_source = _matches_globs(rel_path, settings.test_definition_globs)
    benchmark_source = _matches_globs(rel_path, settings.benchmark_definition_globs)
    formal_source = _matches_globs(rel_path, settings.formal_definition_globs)
    gotcha_source = _matches_globs(rel_path, settings.gotcha_definition_globs)
    adr_source = _matches_globs(rel_path, settings.adr_definition_globs)
    section_two = False

    for line_number, line in enumerate(content.splitlines(), start=1):
        heading = re.match(r"^##\s+2(?:[.\s]|$)", line)
        if heading:
            section_two = True
        elif re.match(r"^##\s+", line):
            section_two = False

        if section_two and "|" in line and (test_source or benchmark_source or formal_source):
            cells = line.split("|")
            if len(cells) > 1:
                first_cell = cells[1].replace("`", "").replace("*", "").strip()
                match = candidate_pattern.fullmatch(first_cell)
                if match:
                    token = match.group(0)
                    family = token.split("-", maxsplit=1)[0].upper()
                    eligible = (
                        (family == "TEST" and test_source)
                        or (family == "BENCHMARK" and benchmark_source)
                        or (family == "FORMAL" and formal_source)
                    )
                    if eligible:
                        line_start = line.find(token)
                        spans.append(
                            _DefinitionSpan(
                                rel_path,
                                line_number,
                                line_start,
                                line_start + len(token),
                                family,
                                token,
                                "case" if family == "TEST" else "specification",
                                _section_for_line(rel_path, line_number, section_map),
                            )
                        )

        if gotcha_source:
            for comment_match in re.finditer(r"<!--\s*definition:\s*(.*?)\s*-->", line):
                for match in candidate_pattern.finditer(comment_match.group(1)):
                    token = match.group(0)
                    if token.split("-", maxsplit=1)[0].upper() == "GOTCHA":
                        line_start = comment_match.start(1) + match.start()
                        spans.append(
                            _DefinitionSpan(
                                rel_path,
                                line_number,
                                line_start,
                                line_start + len(token),
                                "GOTCHA",
                                token,
                                "gotcha",
                                _section_for_line(rel_path, line_number, section_map),
                            )
                        )

        if adr_source and re.match(r"^\s*#{1,6}\s+", line):
            for match in candidate_pattern.finditer(line):
                token = match.group(0)
                if token.split("-", maxsplit=1)[0].upper() == "ADR":
                    spans.append(
                        _DefinitionSpan(
                            rel_path,
                            line_number,
                            match.start(),
                            match.end(),
                            "ADR",
                            token,
                            "decision",
                            _section_for_line(rel_path, line_number, section_map),
                        )
                    )
                    break

    valid_spans = [span for span in spans if _valid_identifier(span.identifier, family_patterns)]
    return valid_spans


def _expand_range(
    start: str,
    end: str | None,
    definitions: list[IdentifierDefinition],
    family_patterns: dict[str, Pattern[str]],
) -> list[str]:
    if end is None:
        return []
    start_family = start.split("-", maxsplit=1)[0].upper()
    end_family = end.split("-", maxsplit=1)[0].upper()
    if start_family != end_family:
        return []
    start_match = family_patterns[start_family].fullmatch(start)
    end_match = family_patterns[end_family].fullmatch(end)
    if start_match is None or end_match is None:
        return []
    start_parts = start_match.groupdict()
    end_parts = end_match.groupdict()
    start_serial = int(start_parts["serial"])
    end_serial = int(end_parts["serial"])
    series_fields = [name for name in start_parts if name not in {"serial", "variant"}]
    if start_serial > end_serial or any(
        start_parts[name] != end_parts[name] for name in series_fields
    ):
        return []
    return sorted(
        {
            definition.identifier
            for definition in definitions
            if definition.family == start_family
            and (definition_match := family_patterns[start_family].fullmatch(definition.identifier))
            is not None
            and all(definition_match.group(name) == start_parts[name] for name in series_fields)
            and start_serial <= int(definition_match.group("serial")) <= end_serial
        }
    )


def _valid_identifier(identifier: str, patterns: dict[str, Pattern[str]]) -> bool:
    family = identifier.split("-", maxsplit=1)[0].upper()
    pattern = patterns.get(family)
    return pattern is not None and pattern.fullmatch(identifier) is not None


def _section_map(rel_path: str, content: str) -> list[tuple[int, str]]:
    return [
        (line_number, f"sec:{rel_path}#{match.group(2).strip()}")
        for line_number, line in enumerate(content.splitlines(), start=1)
        if (match := re.match(r"^(#{1,6})\s+(.+)$", line)) is not None
    ]


def _section_for_line(
    rel_path: str,
    line: int,
    section_map: list[tuple[int, str]],
) -> str:
    index = bisect_right([heading_line for heading_line, _ in section_map], line) - 1
    return section_map[index][1] if index >= 0 else f"sec:{rel_path}"


def _matches_globs(rel_path: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(rel_path, pattern)
        or fnmatch.fnmatchcase(rel_path, pattern.replace("**/", ""))
        for pattern in patterns
    )


def _is_markdown_anchor(rel_path: str, line: str, start: int) -> bool:
    if Path(rel_path).suffix.lower() != ".md" or start == 0 or line[start - 1] != "#":
        return False
    return "](" in line[:start]


def _reference_kind(rel_path: str, valid: bool) -> str:
    if not valid:
        return "invalid_format"
    return "document" if Path(rel_path).suffix.lower() == ".md" else "source"


__all__ = ["IdentifierScan", "next_identifier", "scan_identifiers"]
