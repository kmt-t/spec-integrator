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
                                    "No definition found in designated source of truth."
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

        return issues
