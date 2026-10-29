"""Identifier definition, reference, format, and sequence checks."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Pattern

from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.models import VerificationIssue


class IdentifierGate:
    """Validate the full-source identifier index built with each workspace."""

    gate = "Identifier"

    def __init__(self, config: Config):
        self.config = config
        self.patterns: dict[str, Pattern[str]] = {
            family: re.compile(pattern) for family, pattern in config.identifiers.patterns.items()
        }

    def verify(self, db: DocAuditDB) -> list[VerificationIssue]:
        assert self.config.identifiers.enabled
        definitions = db.get_identifiers()
        references = db.get_identifier_references()
        definitions_by_id: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        for item in definitions:
            key = (str(item["family"]), str(item["normalized_id"]))
            definitions_by_id[key].append(item)

        issues: list[VerificationIssue] = []
        issues.extend(self._format_issues(definitions, references))
        issues.extend(self._duplicate_issues(definitions_by_id))
        issues.extend(self._unresolved_issues(references, definitions_by_id))
        issues.extend(self._unreferenced_warnings(definitions, references))
        issues.extend(self._sequence_gap_reports(definitions))
        return issues

    def _format_issues(
        self,
        definitions: list[dict[str, object]],
        references: list[dict[str, object]],
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for item in [*definitions, *references]:
            family = str(item["family"])
            identifier = str(item.get("identifier", item.get("normalized_id", "")))
            if self._valid(family, identifier):
                continue
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity="ERROR",
                    file_path=str(item["file_path"]),
                    line=int(item["line"]),
                    rule_code="ID-INVALID-FORMAT",
                    message=f"Identifier does not match the configured {family} syntax: {identifier}",
                )
            )
            range_end = item.get("range_end")
            if range_end is not None and not self._valid(family, str(range_end)):
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity="ERROR",
                        file_path=str(item["file_path"]),
                        line=int(item["line"]),
                        rule_code="ID-INVALID-FORMAT",
                        message=(
                            f"Range endpoint does not match the configured {family} syntax: "
                            f"{range_end}"
                        ),
                    )
                )
        for item in references:
            range_end = item.get("range_end")
            if range_end is None or not self._valid(str(item["family"]), str(item["identifier"])):
                continue
            if self._valid(str(item["family"]), str(range_end)) and not self._valid_range(
                str(item["family"]), str(item["identifier"]), str(range_end)
            ):
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity="ERROR",
                        file_path=str(item["file_path"]),
                        line=int(item["line"]),
                        rule_code="ID-INVALID-RANGE",
                        message=f"Range endpoints do not form one ordered identifier series: {item['raw_text']}",
                    )
                )
        return issues

    def _duplicate_issues(
        self,
        definitions_by_id: dict[tuple[str, str], list[dict[str, object]]],
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for (family, identifier), rows in definitions_by_id.items():
            if len(rows) < 2:
                continue
            locations = ", ".join(f"{row['file_path']}:{row['line']}" for row in rows)
            for row in rows:
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity="ERROR",
                        file_path=str(row["file_path"]),
                        line=int(row["line"]),
                        rule_code="ID-DUPLICATE-DEFINITION",
                        message=f"{family} identifier {identifier} has multiple definitions: {locations}",
                    )
                )
        return issues

    def _unresolved_issues(
        self,
        references: list[dict[str, object]],
        definitions_by_id: dict[tuple[str, str], list[dict[str, object]]],
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for item in references:
            family = str(item["family"])
            identifier = str(item["identifier"])
            candidates = [identifier]
            range_end = item.get("range_end")
            if range_end is not None:
                candidates.append(str(range_end))
            for candidate in candidates:
                if not self._valid(family, candidate):
                    continue
                if definitions_by_id.get((family, candidate)):
                    continue
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity="ERROR",
                        file_path=str(item["file_path"]),
                        line=int(item["line"]),
                        rule_code="ID-UNRESOLVED-REFERENCE",
                        message=f"{family} identifier reference has no definition: {candidate}",
                    )
                )
        return issues

    def _unreferenced_warnings(
        self,
        definitions: list[dict[str, object]],
        references: list[dict[str, object]],
    ) -> list[VerificationIssue]:
        referenced: set[tuple[str, str]] = set()
        for item in references:
            family = str(item["family"])
            identifier = str(item["identifier"])
            if self._valid(family, identifier):
                referenced.add((family, identifier))
            range_end = item.get("range_end")
            if range_end is not None and self._valid(family, str(range_end)):
                referenced.add((family, str(range_end)))
            raw_expanded = item.get("expanded_ids", "[]")
            expanded = (
                raw_expanded if isinstance(raw_expanded, list) else json.loads(str(raw_expanded))
            )
            if isinstance(expanded, list):
                for identifier_in_range in expanded:
                    if isinstance(identifier_in_range, str):
                        referenced.add((family, identifier_in_range))

        issues: list[VerificationIssue] = []
        for item in definitions:
            family = str(item["family"])
            identifier = str(item["normalized_id"])
            if family not in {"TEST", "GOTCHA", "BENCHMARK"}:
                continue
            if (family, identifier) in referenced:
                continue
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity="WARNING",
                    file_path=str(item["file_path"]),
                    line=int(item["line"]),
                    rule_code="ID-UNREFERENCED-DEFINITION",
                    message=f"{family} identifier has no indexed references: {identifier}",
                )
            )
        return issues

    def _sequence_gap_reports(
        self, definitions: list[dict[str, object]]
    ) -> list[VerificationIssue]:
        grouped: dict[tuple[str, tuple[str, ...]], set[int]] = defaultdict(set)
        for item in definitions:
            family = str(item["family"])
            identifier = str(item["normalized_id"])
            if not self._valid(family, identifier):
                continue
            match = self.patterns[family].fullmatch(identifier)
            assert match is not None
            parts = match.groupdict()
            serial_text = parts.get("serial")
            assert serial_text is not None
            series = tuple(
                value
                for name, value in parts.items()
                if name not in {"serial", "variant"} and value is not None
            )
            grouped[(family, series)].add(int(serial_text))

        counts: dict[str, tuple[int, int]] = {}
        for (family, _series), serials in grouped.items():
            if not serials:
                continue
            gap_count = max(serials) - min(serials) + 1 - len(serials)
            previous_gaps, previous_series = counts.get(family, (0, 0))
            counts[family] = (previous_gaps + gap_count, previous_series + int(gap_count > 0))

        return [
            VerificationIssue(
                gate=self.gate,
                severity="INFO",
                file_path="-",
                line=0,
                rule_code="ID-SERIAL-GAPS",
                message=f"{family}: {gap_count} missing serial numbers in {series_count} series.",
            )
            for family, (gap_count, series_count) in sorted(counts.items())
            if gap_count > 0
        ]

    def _valid(self, family: str, identifier: str) -> bool:
        pattern = self.patterns.get(family)
        return pattern is not None and pattern.fullmatch(identifier) is not None

    def _valid_range(self, family: str, start: str, end: str) -> bool:
        pattern = self.patterns.get(family)
        if pattern is None:
            return False
        start_match = pattern.fullmatch(start)
        end_match = pattern.fullmatch(end)
        if start_match is None or end_match is None:
            return False
        start_parts = start_match.groupdict()
        end_parts = end_match.groupdict()
        series_names = [name for name in start_parts if name not in {"serial", "variant"}]
        start_serial = start_parts.get("serial")
        end_serial = end_parts.get("serial")
        assert start_serial is not None and end_serial is not None
        return all(start_parts[name] == end_parts[name] for name in series_names) and int(
            start_serial
        ) <= int(end_serial)


__all__ = ["IdentifierGate"]
