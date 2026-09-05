from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ===========================================================================
# Domain Data Models (Entities & DTOs)
# ===========================================================================


# ---------------------------------------------------------------------------
# Verification Issue (Quality Gate findings)
# ---------------------------------------------------------------------------
@dataclass
class VerificationIssue:
    gate: str  # "Format", "Traceability", "Hierarchy", "Formal", "WIT", "Evidence", "Obligation", "Consistency"
    severity: str  # "ERROR" or "WARNING"
    file_path: str
    line: int
    rule_code: str
    message: str


# ---------------------------------------------------------------------------
# Document & AST Parsed Models
# ---------------------------------------------------------------------------
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
    tags: list[str] = field(default_factory=list)  # {VERIFY_FORMAL}, {VERIFY_LLM}, etc.
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


# ---------------------------------------------------------------------------
# Verifier Results
# ---------------------------------------------------------------------------
@dataclass
class PropertyResult:
    name: str
    kind: str
    status: str  # "PASS", "FAIL", "VACUOUS", "INVALID", "REFUTED"
    details: str = ""


@dataclass
class FormalModelResult:
    component: str
    model_file: str
    status: str  # "PASS", "FAIL", "ERROR", "NOT_FOUND", "NO_CONTRACT", "VACUOUS"
    details: str = ""
    invariants: list[dict] = field(default_factory=list)
    properties: list[PropertyResult] = field(default_factory=list)
    backing_documents: list[str] = field(default_factory=list)
    audit: dict = field(default_factory=dict)


@dataclass
class WITFileResult:
    component: str
    wit_file: str
    status: str  # "PASS", "FAIL", "NOT_FOUND"
    details: str = ""
    defined_interfaces: list[str] = field(default_factory=list)
    defined_worlds: list[str] = field(default_factory=list)


@dataclass
class SymbolDrift:
    symbol: str
    values: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ConsistencySummary:
    invariants_checked: int = 0
    symbols_tracked: int = 0
    drifting_symbols: list[SymbolDrift] = field(default_factory=list)


@dataclass
class ObligationSummary:
    assessed_documents: int = 0
    stale_documents: list[str] = field(default_factory=list)
    unassessed_documents: list[str] = field(default_factory=list)
    demanded: int = 0
    discharged: int = 0
    skipped: list[dict] = field(default_factory=list)
    judge_missing: list[str] = field(default_factory=list)
    document_judge_missing: list[str] = field(default_factory=list)
    keywords_total: int = 0
    keywords_assessed: int = 0

    @property
    def coverage(self) -> float:
        if self.demanded == 0:
            return 1.0
        return self.discharged / self.demanded


# ---------------------------------------------------------------------------
# Judge & Assessor Results
# ---------------------------------------------------------------------------
@dataclass
class JudgeResult:
    item_id: str
    item_label: str
    status: str  # "PASS", "WARN", "FAIL", "SKIPPED"
    summary: str
    issues: list[dict] = field(default_factory=list)
    covered_files: list[str] = field(default_factory=list)


@dataclass
class JudgeReport:
    results: list[JudgeResult] = field(default_factory=list)
    total_evaluated: int = 0
    pass_count: int = 0
    warn_count: int = 0
    fail_count: int = 0

    def __iter__(self):
        return iter(self.results)

    def __len__(self):
        return len(self.results)

    def __getitem__(self, idx):
        return self.results[idx]


@dataclass
class KeywordRiskAssessment:
    item_id: str
    keyword: str
    file_path: str
    tier: str | int
    complexity_score: int
    risk_score: int
    line: int = 1
    covered_files: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class RiskAssessmentReport:
    assessments: list[KeywordRiskAssessment] = field(default_factory=list)
    total_evaluated: int = 0
    high_risk_count: int = 0

    def to_markdown(
        self, project_name: str = "Specification Project", risk_threshold: int = 4
    ) -> str:
        lines = [
            f"# {project_name} 設計複雑度 & リスク評価レポート (Risk Assessment Report)",
            "",
            f"- **評価キーワード総数**: {self.total_evaluated}",
            f"- **高リスク（{risk_threshold}/5 以上）キーワード数**: {self.high_risk_count}",
            "",
            "---",
            "",
            f"## 1. 高リスクキーワード（{risk_threshold}/5 以上、`{{VERIFY_LLM}}` を要求）",
            "",
            "| キーワード | 定義ファイル | 複雑度 | リスク | 評価サマリー |",
            "| :--- | :--- | :---: | :---: | :--- |",
        ]
        high_risk = [a for a in self.assessments if a.risk_score >= risk_threshold]
        high_risk.sort(key=lambda x: x.risk_score, reverse=True)
        for a in high_risk:
            lines.append(
                f"| `{{{a.keyword}}}` | `{a.file_path}` | {a.complexity_score}/5 | "
                f"**{a.risk_score}/5** | {a.summary} |"
            )

        lines.extend(
            [
                "",
                "---",
                "",
                "## 2. 全キーワードの複雑度・リスク評価一覧 (降順)",
                "",
                "| キーワード | 定義ファイル | Tier | 複雑度 | リスク | 評価サマリー |",
                "| :--- | :--- | :---: | :---: | :---: | :--- |",
            ]
        )
        all_sorted = sorted(self.assessments, key=lambda x: x.risk_score, reverse=True)
        for a in all_sorted:
            lines.append(
                f"| `{{{a.keyword}}}` | `{a.file_path}` | {a.tier} | "
                f"{a.complexity_score}/5 | {a.risk_score}/5 | {a.summary} |"
            )
        return "\n".join(lines)


@dataclass
class TestChainTarget:
    __test__ = False
    component_name: str
    design_doc_path: Path
    test_spec_path: Path
    test_code_paths: list[Path]


@dataclass
class TestChainResult:
    __test__ = False
    component_name: str
    design_doc: str
    test_spec: str
    test_code_files: list[str]
    status: str  # "PASS", "WARN", "FAIL", "SKIPPED"
    summary: str
    issues: list[dict] = field(default_factory=list)


@dataclass
class TestChainReport:
    __test__ = False
    results: list[TestChainResult] = field(default_factory=list)
    total_evaluated: int = 0
    pass_count: int = 0
    warn_count: int = 0
    fail_count: int = 0

    def to_markdown(self, project_name: str = "System Specification") -> str:
        lines = [
            f"# {project_name} 設計仕様→テスト仕様→テストコード 一貫性監査レポート (LLM as a Judge)",
            "",
            f"- **監査コンポーネント総数**: {self.total_evaluated}",
            f"- **合格 (PASS)**: {self.pass_count}",
            f"- **警告 (WARN)**: {self.warn_count}",
            f"- **不合格 (FAIL)**: {self.fail_count}",
            "",
            "---",
            "",
            "## 1. 検出された不一致・網羅性課題 (Issues Found)",
            "",
        ]
        issues_found = False
        for r in self.results:
            if r.status in ("WARN", "FAIL") or r.issues:
                issues_found = True
                badge = "🔴 FAIL" if r.status == "FAIL" else "🟡 WARN"
                lines.append(f"### {badge}: `{r.component_name}`")
                lines.append(f"- **設計仕様書**: `{r.design_doc}`")
                lines.append(f"- **テスト仕様書**: `{r.test_spec}`")
                lines.append(
                    f"- **テストコード**: {', '.join(f'`{f}`' for f in r.test_code_files) if r.test_code_files else 'なし'}"
                )
                lines.append(f"- **評価サマリー**: {r.summary}")
                if r.issues:
                    lines.append("- **検出項目**:")
                    for iss in r.issues:
                        sev = iss.get("severity", "WARNING")
                        layer = iss.get("layer", "")
                        loc = iss.get("location", "Unknown")
                        desc = iss.get("description", "")
                        lines.append(f"  - **[{sev}] [{layer}]** `{loc}`: {desc}")
                lines.append("")

        if not issues_found:
            lines.append(
                "✔ 評価されたすべてのコンポーネントにおいて、設計仕様 $\\to$ テスト仕様 $\\to$ テスト実装コード間の重大な不一致・欠落は検出されませんでした。\n"
            )

        lines.extend(
            [
                "---",
                "",
                "## 2. 全コンポーネント評価一覧",
                "",
                "| コンポーネント | 判定 | 評価サマリー | 検出Issue数 |",
                "| :--- | :---: | :--- | :---: |",
            ]
        )
        for r in self.results:
            badge = (
                "🟢 PASS"
                if r.status == "PASS"
                else ("🟡 WARN" if r.status == "WARN" else "🔴 FAIL")
            )
            lines.append(f"| `{r.component_name}` | {badge} | {r.summary} | {len(r.issues)} |")
        return "\n".join(lines)


__all__ = [
    "ConsistencySummary",
    "FormalModelResult",
    "JudgeReport",
    "JudgeResult",
    "KeywordRiskAssessment",
    "ObligationSummary",
    "ParsedDocument",
    "ParsedLink",
    "ParsedSection",
    "PropertyResult",
    "RiskAssessmentReport",
    "SymbolDrift",
    "TestChainReport",
    "TestChainResult",
    "TestChainTarget",
    "VerificationIssue",
    "WITFileResult",
]
