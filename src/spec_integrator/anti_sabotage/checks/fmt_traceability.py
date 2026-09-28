from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import ParsedDocument, ParsedSection, VerificationIssue


class TraceabilityCheck(AntiSabotageCheck):
    """トレーサビリティ検証: 文書内の定義宣言と参照を照合する。"""

    rule_code = "TRACE-UNDEFINED-KEYWORD"
    name = "キーワード参照・要件の欠落"
    gate = "Traceability"
    severity = "ERROR"
    description = "未定義のキーワード参照や下位 Tier で未参照の Tier 0 要件を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        definitions: dict[str, list[tuple[ParsedDocument, ParsedSection]]] = {}
        for doc in ctx.documents:
            for section in doc.sections:
                candidates = set(section.canonical_definition_keywords)
                candidates.update(section.definition_keywords)
                for keyword in candidates:
                    if ctx.config.is_keyword_definition(
                        keyword, doc.file_path, section.canonical_definition_keywords
                    ):
                        definitions.setdefault(keyword, []).append((doc, section))

        self._check_index_has_source_markers(ctx, issues)
        self._check_definition_has_inline_marker(ctx, issues)
        defined_keywords = set(definitions)
        defined_in_tier0 = {
            keyword
            for keyword, locations in definitions.items()
            if any(doc.tier == 0 for doc, _section in locations)
        }

        referenced_keywords: set[str] = set()
        for doc in ctx.documents:
            for section in doc.sections:
                for keyword in section.keywords:
                    if ctx.config.is_keyword_definition(
                        keyword, doc.file_path, section.canonical_definition_keywords
                    ):
                        continue
                    referenced_keywords.add(keyword)
                    if keyword not in defined_keywords:
                        issues.append(
                            VerificationIssue(
                                gate="Traceability",
                                severity="ERROR",
                                file_path=doc.file_path,
                                line=section.line_start,
                                rule_code="TRACE-UNDEFINED-KEYWORD",
                                message=(
                                    f"Undefined keyword referenced: '{{{keyword}}}'. "
                                    "Reason: No canonical definition declaration was found. "
                                    "Check the source section and its definition marker."
                                ),
                            )
                        )

                overlap = sorted(
                    set(section.canonical_definition_keywords)
                    .union(
                        keyword
                        for keyword in section.definition_keywords
                        if ctx.config.is_keyword_definition(
                            keyword, doc.file_path, section.canonical_definition_keywords
                        )
                    )
                    .intersection(section.reference_keywords)
                )
                for keyword in overlap:
                    issues.append(
                        VerificationIssue(
                            gate="Traceability",
                            severity="ERROR",
                            file_path=doc.file_path,
                            line=section.line_start,
                            rule_code="TRACE-DUPLICATE-DEF-REF",
                            message=(
                                f"Keyword '{{{keyword}}}' is both defined inline and referenced "
                                f"in a traceability comment in section '{section.heading}'. "
                                "A section must either define the keyword or reference it."
                            ),
                        )
                    )

        for keyword in sorted(defined_in_tier0):
            if keyword not in referenced_keywords:
                locations = definitions[keyword]
                def_file = next(doc.file_path for doc, _section in locations if doc.tier == 0)
                issues.append(
                    VerificationIssue(
                        gate="Traceability",
                        severity="ERROR",
                        file_path=def_file,
                        line=1,
                        rule_code="TRACE-UNREFERENCED-REQUIREMENT",
                        message=(
                            f"Requirement '{{{keyword}}}' is defined in Tier 0 but never "
                            "referenced or refined in downstream component specs."
                        ),
                    )
                )

        return issues

    def _check_index_has_source_markers(
        self, ctx: AntiSabotageContext, issues: list[VerificationIssue]
    ) -> None:
        registry = ctx.config.keyword_registry_sources
        if not registry:
            return

        for keyword, indexed_source in sorted(registry.items()):
            if indexed_source is None:
                issues.append(
                    VerificationIssue(
                        gate="Traceability",
                        severity="ERROR",
                        file_path="architecture/document_structure.md",
                        line=1,
                        rule_code="TRACE-INVALID-INDEX-LOCATION",
                        message=(
                            f"Legacy keyword source for '{{{keyword}}}' does not resolve to one path. "
                            "Replace it with an in-document definition declaration."
                        ),
                    )
                )
                continue

            source_doc = next(
                (doc for doc in ctx.documents if doc.file_path == indexed_source), None
            )
            has_inline_definition = source_doc is not None and any(
                keyword in section.canonical_definition_keywords
                or (
                    keyword in section.definition_keywords
                    and ctx.config.is_keyword_definition(keyword, indexed_source)
                )
                for section in source_doc.sections
            )
            if not has_inline_definition:
                issues.append(
                    VerificationIssue(
                        gate="Traceability",
                        severity="ERROR",
                        file_path=indexed_source,
                        line=1,
                        rule_code="TRACE-INDEX-WITHOUT-DEFINITION",
                        message=(
                            f"Legacy keyword source for '{{{keyword}}}' points to '{indexed_source}', "
                            "but that document has no in-document definition declaration."
                        ),
                    )
                )

    def _check_definition_has_inline_marker(
        self, ctx: AntiSabotageContext, issues: list[VerificationIssue]
    ) -> None:
        for doc in ctx.documents:
            for section in doc.sections:
                for keyword in section.canonical_definition_keywords:
                    if keyword in section.definition_keywords:
                        continue
                    issues.append(
                        VerificationIssue(
                            gate="Traceability",
                            severity="ERROR",
                            file_path=doc.file_path,
                            line=section.line_start,
                            rule_code="TRACE-DEFINITION-WITHOUT-INLINE-MARKER",
                            message=(
                                f"Canonical definition declaration for '{{{keyword}}}' has no "
                                "matching inline keyword in the section body."
                            ),
                        )
                    )
