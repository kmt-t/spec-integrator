from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class TraceabilityCheck(AntiSabotageCheck):
    """トレーサビリティ検証: 未定義キーワードの参照および Tier 0 要件の未参照を検証する。"""

    rule_code = "TRACE-UNDEFINED-KEYWORD"
    name = "キーワード参照・要件の欠落"
    gate = "Traceability"
    severity = "ERROR"
    description = "未定義のキーワード参照や下位 Tier で未参照の Tier 0 要件を検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        defined_keywords: dict[str, str] = {}
        defined_in_tier0: set[str] = set()

        for doc in ctx.documents:
            is_t0 = doc.tier == 0
            for kw in doc.all_keywords:
                if ctx.config.is_keyword_definition(kw, doc.file_path):
                    defined_keywords[kw] = doc.file_path
                    if is_t0:
                        defined_in_tier0.add(kw)

        referenced_keywords: set[str] = set()
        for doc in ctx.documents:
            for sec in doc.sections:
                for kw in sec.keywords:
                    if ctx.config.is_keyword_definition(kw, doc.file_path):
                        continue
                    referenced_keywords.add(kw)
                    if kw not in defined_keywords:
                        issues.append(
                            VerificationIssue(
                                gate="Traceability",
                                severity="ERROR",
                                file_path=doc.file_path,
                                line=sec.line_start,
                                rule_code="TRACE-UNDEFINED-KEYWORD",
                                message=(
                                    f"Undefined keyword referenced: '{{{kw}}}'. "
                                    "Reason: No definition found in designated source of truth. "
                                    "Check the rules in 'docs/architecture/document_structure.md' and 'docs/architecture/keyword_dictionary.md'."
                                ),
                            )
                        )

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
                        message=(
                            f"Requirement '{{{kw}}}' is defined in Tier 0 but never "
                            "referenced or refined in downstream component specs."
                        ),
                    )
                )

        # Check for same keyword defined (inline) and referenced (traceability comment) in the same section
        import re
        for doc in ctx.documents:
            lines = doc.content.splitlines()
            for sec in doc.sections:
                sec_lines = lines[sec.line_start - 1 : sec.line_end]
                comment_kws: set[str] = set()
                inline_kws: set[str] = set()
                in_code = False
                for line in sec_lines:
                    s_line = line.strip()
                    if s_line.startswith("```"):
                        in_code = not in_code
                        continue
                    if in_code:
                        continue
                    for m_tr in re.finditer(r"<!--\s*traceability:\s*(.*?)\s*-->", line):
                        comment_kws.update(re.findall(r"\{([A-Za-z0-9_\-]+)\}", m_tr.group(1)))
                    line_no_comment = re.sub(r"<!--.*?-->", "", line)
                    for m_in in re.finditer(r"\{([A-Za-z0-9_\-]+)\}", line_no_comment):
                        kw_name = m_in.group(1)
                        if not kw_name.startswith("VERIFY_"):
                            inline_kws.add(kw_name)

                overlap = sorted(comment_kws.intersection(inline_kws))
                for kw in overlap:
                    issues.append(
                        VerificationIssue(
                            gate="Traceability",
                            severity="ERROR",
                            file_path=doc.file_path,
                            line=sec.line_start,
                            rule_code="TRACE-DUPLICATE-DEF-REF",
                            message=(
                                f"Keyword '{{{kw}}}' is both defined (inline) and referenced (comment) in the same section '{sec.heading}'. "
                                "Reason: A keyword must be either defined inline OR referenced via '<!-- traceability: ... -->', never both in the same section. "
                                "Check the rules in 'docs/architecture/document_structure.md' and 'docs/architecture/keyword_dictionary.md'."
                            ),
                        )
                    )

        return issues
