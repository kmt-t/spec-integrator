from __future__ import annotations

import datetime
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# ===========================================================================
# 1. Unified Domain Data Models (Entities & DTOs)
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
    cochange_tracked: int = 0
    cochange_stale: list[dict] = field(default_factory=list)
    baseline_present: bool = False


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


# ===========================================================================
# 2. Database Persistence Layer (SQLite DAO)
# ===========================================================================
class DocAuditDB:
    """Unified SQLite persistence for document topologies, formal models,

    quality gate issues, risk assessments, and LLM judge results.
    """

    def __init__(self, db_path: Path | str = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        # Performance tuning
        self.conn.execute("PRAGMA synchronous = OFF")
        self.conn.execute("PRAGMA journal_mode = MEMORY")
        self.create_tables()

    def create_tables(self):
        with self.conn:
            # 1. documents
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    file_path TEXT PRIMARY KEY,
                    tier TEXT,
                    component TEXT,
                    content_hash TEXT,
                    updated_at TEXT
                )
            """)
            # 2. sections
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS sections (
                    section_id TEXT PRIMARY KEY,
                    file_path TEXT,
                    heading TEXT,
                    level INTEGER,
                    line_start INTEGER,
                    line_end INTEGER,
                    body_text TEXT,
                    content_hash TEXT,
                    FOREIGN KEY(file_path) REFERENCES documents(file_path)
                )
            """)
            # 3. keywords
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    keyword TEXT PRIMARY KEY,
                    category TEXT,
                    defined_in_file TEXT,
                    defined_in_section TEXT,
                    description TEXT
                )
            """)
            # 4. keyword_references
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS keyword_references (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT,
                    file_path TEXT,
                    section_id TEXT,
                    relation_type TEXT,
                    line_number INTEGER,
                    FOREIGN KEY(keyword) REFERENCES keywords(keyword),
                    FOREIGN KEY(section_id) REFERENCES sections(section_id)
                )
            """)
            # 5. document_links
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS document_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_file TEXT,
                    source_line INTEGER,
                    target_path TEXT,
                    target_anchor TEXT,
                    is_valid INTEGER DEFAULT 1
                )
            """)
            # 6. formal_models
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS formal_models (
                    component TEXT PRIMARY KEY,
                    model_path TEXT,
                    framework TEXT,
                    status TEXT,
                    details TEXT,
                    checked_at TEXT
                )
            """)
            # 7. wit_files
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS wit_files (
                    component TEXT PRIMARY KEY,
                    wit_file TEXT,
                    status TEXT,
                    details TEXT,
                    defined_interfaces TEXT,
                    defined_worlds TEXT,
                    checked_at TEXT
                )
            """)
            # 8. verification_issues
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS verification_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gate TEXT,
                    severity TEXT,
                    file_path TEXT,
                    line INTEGER,
                    rule_code TEXT,
                    message TEXT,
                    recorded_at TEXT
                )
            """)
            # 9. audit_cache
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_cache (
                    hash_key TEXT PRIMARY KEY,
                    rule_code TEXT,
                    target_id TEXT,
                    status TEXT,
                    reason TEXT,
                    updated_at TEXT
                )
            """)
            # 10. risk_assessments
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_assessments (
                    item_id TEXT PRIMARY KEY,
                    keyword TEXT,
                    file_path TEXT,
                    tier TEXT,
                    complexity_score INTEGER,
                    risk_score INTEGER,
                    line INTEGER,
                    covered_files TEXT,
                    summary TEXT,
                    generated_at TEXT
                )
            """)
            # 11. judge_results
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS judge_results (
                    item_id TEXT PRIMARY KEY,
                    item_label TEXT,
                    status TEXT,
                    summary TEXT,
                    covered_files TEXT,
                    generated_at TEXT
                )
            """)
            # 12. document_judge_results
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS document_judge_results (
                    item_id TEXT PRIMARY KEY,
                    item_label TEXT,
                    status TEXT,
                    summary TEXT,
                    covered_files TEXT,
                    generated_at TEXT
                )
            """)
            # 13. test_chain_results
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS test_chain_results (
                    component_name TEXT PRIMARY KEY,
                    status TEXT,
                    summary TEXT,
                    generated_at TEXT
                )
            """)
            # 14. run_metadata
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS run_metadata (
                    run_type TEXT PRIMARY KEY,
                    backend TEXT,
                    generated_at TEXT
                )
            """)
            # 15. assessed_doc_hashes
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS assessed_doc_hashes (
                    run_type TEXT,
                    file_path TEXT,
                    content_hash TEXT,
                    PRIMARY KEY (run_type, file_path)
                )
            """)
            # 16. consistency_baseline
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS consistency_baseline (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER,
                    data_json TEXT,
                    updated_at TEXT
                )
            """)

    def _now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def clear_all(self):
        """Clears structural document models and caches while preserving costly LLM assessments."""
        with self.conn:
            tables = [
                "documents",
                "sections",
                "keywords",
                "keyword_references",
                "document_links",
                "formal_models",
                "wit_files",
                "verification_issues",
                "audit_cache",
            ]
            for t in tables:
                self.conn.execute(f"DELETE FROM {t}")

    def reset_all(self):
        """Completely resets all tables including LLM judgments."""
        with self.conn:
            tables = [
                "documents",
                "sections",
                "keywords",
                "keyword_references",
                "document_links",
                "formal_models",
                "wit_files",
                "verification_issues",
                "audit_cache",
                "risk_assessments",
                "judge_results",
                "document_judge_results",
                "test_chain_results",
                "run_metadata",
                "assessed_doc_hashes",
            ]
            for t in tables:
                self.conn.execute(f"DELETE FROM {t}")

    def commit(self):
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Document & Section Operations
    # ------------------------------------------------------------------ #
    def insert_document(
        self,
        file_path: str,
        tier: int | str | None,
        component: str,
        content_hash: str,
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO documents (file_path, tier, component, content_hash, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    file_path,
                    str(tier) if tier is not None else None,
                    component,
                    content_hash,
                    self._now(),
                ),
            )

    def insert_section(
        self,
        section_id: str,
        file_path: str,
        heading: str,
        level: int,
        line_start: int,
        line_end: int,
        body_text: str,
        content_hash: str,
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO sections
                (section_id, file_path, heading, level, line_start, line_end, body_text, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    section_id,
                    file_path,
                    heading,
                    level,
                    line_start,
                    line_end,
                    body_text,
                    content_hash,
                ),
            )

    def insert_keyword_reference(
        self,
        keyword: str,
        file_path: str,
        section_id: str,
        relation_type: str,
        line_number: int,
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO keyword_references (keyword, file_path, section_id, relation_type, line_number)
                VALUES (?, ?, ?, ?, ?)
            """,
                (keyword, file_path, section_id, relation_type, line_number),
            )

    def insert_link(
        self,
        source_file: str,
        source_line: int,
        target_path: str,
        target_anchor: str,
        is_valid: int = 1,
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO document_links (source_file, source_line, target_path, target_anchor, is_valid)
                VALUES (?, ?, ?, ?, ?)
            """,
                (source_file, source_line, target_path, target_anchor, is_valid),
            )

    def get_all_documents(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM documents ORDER BY file_path")
        return [dict(row) for row in cursor.fetchall()]

    def get_all_sections(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM sections ORDER BY file_path, line_start")
        return [dict(row) for row in cursor.fetchall()]

    def get_keyword_references(self, keyword: str) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM keyword_references WHERE keyword = ?", (keyword,))
        return [dict(row) for row in cursor.fetchall()]

    def get_invalid_links(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM document_links WHERE is_valid = 0")
        return [dict(row) for row in cursor.fetchall()]

    def set_cache(
        self,
        hash_key: str,
        rule_code: str,
        target_id: str,
        status: str,
        reason: str = "",
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO audit_cache (hash_key, rule_code, target_id, status, reason, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (hash_key, rule_code, target_id, status, reason, self._now()),
            )

    def get_cache(self, hash_key: str) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM audit_cache WHERE hash_key = ?", (hash_key,))
        row = cursor.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Formal Models & WIT Files
    # ------------------------------------------------------------------ #
    def insert_formal_model(
        self,
        component: str,
        model_path: str,
        framework: str,
        status: str,
        details: str = "",
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO formal_models
                (component, model_path, framework, status, details, checked_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (component, model_path, framework, status, details, self._now()),
            )

    def insert_wit_file(
        self,
        component: str,
        wit_file: str,
        status: str,
        details: str = "",
        defined_interfaces: list[str] | None = None,
        defined_worlds: list[str] | None = None,
    ):
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO wit_files
                (component, wit_file, status, details, defined_interfaces, defined_worlds, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    component,
                    wit_file,
                    status,
                    details,
                    json.dumps(defined_interfaces or []),
                    json.dumps(defined_worlds or []),
                    self._now(),
                ),
            )

    def get_formal_models(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM formal_models ORDER BY component")
        return [dict(row) for row in cursor.fetchall()]

    def get_wit_files(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM wit_files ORDER BY component")
        rows = []
        for r in cursor.fetchall():
            d = dict(r)
            d["defined_interfaces"] = json.loads(d.get("defined_interfaces") or "[]")
            d["defined_worlds"] = json.loads(d.get("defined_worlds") or "[]")
            rows.append(d)
        return rows

    # ------------------------------------------------------------------ #
    # Verification Issues Persistence
    # ------------------------------------------------------------------ #
    def replace_verification_issues(self, issues: list[VerificationIssue]) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute("DELETE FROM verification_issues")
            for iss in issues:
                self.conn.execute(
                    """
                    INSERT INTO verification_issues
                    (gate, severity, file_path, line, rule_code, message, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        iss.gate,
                        iss.severity,
                        iss.file_path,
                        iss.line,
                        iss.rule_code,
                        iss.message,
                        now,
                    ),
                )

    def get_verification_issues(self) -> list[VerificationIssue]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM verification_issues ORDER BY file_path, line")
        return [
            VerificationIssue(
                gate=r["gate"],
                severity=r["severity"],
                file_path=r["file_path"],
                line=r["line"],
                rule_code=r["rule_code"],
                message=r["message"],
            )
            for r in cursor.fetchall()
        ]

    # ------------------------------------------------------------------ #
    # Risk Assessments (`llm-assess`)
    # ------------------------------------------------------------------ #
    def _set_run_metadata(self, run_type: str, backend: str, now: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO run_metadata (run_type, backend, generated_at)
            VALUES (?, ?, ?)
            """,
            (run_type, backend, now),
        )

    def _unpack_covered_files(self, rows) -> list[dict]:
        out = []
        for row in rows:
            d = dict(row)
            raw = d.get("covered_files")
            if isinstance(raw, str):
                try:
                    d["covered_files"] = json.loads(raw)
                except Exception:
                    d["covered_files"] = []
            elif not isinstance(raw, list):
                d["covered_files"] = []
            out.append(d)
        return out

    def replace_risk_assessments(
        self, rows: list[dict | KeywordRiskAssessment], backend: str
    ) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute("DELETE FROM risk_assessments")
            for r in rows:
                if isinstance(r, KeywordRiskAssessment):
                    item_id = r.item_id
                    keyword = r.keyword
                    file_path = r.file_path
                    tier = str(r.tier) if r.tier is not None else None
                    complexity = r.complexity_score
                    risk = r.risk_score
                    line = r.line
                    covered = json.dumps(r.covered_files)
                    summary = r.summary
                else:
                    item_id = r["item_id"]
                    keyword = r["keyword"]
                    file_path = r["file_path"]
                    tier = str(r.get("tier")) if r.get("tier") is not None else None
                    complexity = r.get("complexity_score", 0)
                    risk = r["risk_score"]
                    line = r.get("line", 1)
                    covered = json.dumps(r.get("covered_files", []))
                    summary = r.get("summary", "")

                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO risk_assessments
                    (item_id, keyword, file_path, tier, complexity_score, risk_score,
                     line, covered_files, summary, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        keyword,
                        file_path,
                        tier,
                        complexity,
                        risk,
                        line,
                        covered,
                        summary,
                        now,
                    ),
                )
            self._set_run_metadata("risk_assessment", backend, now)

    def get_risk_assessments(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM risk_assessments ORDER BY risk_score DESC")
        return self._unpack_covered_files(cursor.fetchall())

    # ------------------------------------------------------------------ #
    # Judge results (`llm-judge`)
    # ------------------------------------------------------------------ #
    def replace_judge_results(self, rows: list[dict | JudgeResult], backend: str) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute("DELETE FROM judge_results")
            for r in rows:
                if isinstance(r, JudgeResult):
                    item_id = r.item_id
                    item_label = r.item_label
                    status = r.status
                    summary = r.summary
                    covered = json.dumps(r.covered_files)
                else:
                    item_id = r["item_id"]
                    item_label = r["item_label"]
                    status = r["status"]
                    summary = r.get("summary", "")
                    covered = json.dumps(r.get("covered_files", []))

                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO judge_results
                    (item_id, item_label, status, summary, covered_files, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (item_id, item_label, status, summary, covered, now),
                )
            self._set_run_metadata("judge", backend, now)

    def get_judge_results(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM judge_results ORDER BY item_label")
        return self._unpack_covered_files(cursor.fetchall())

    # ------------------------------------------------------------------ #
    # Whole-document judge results (`llm-judge`)
    # ------------------------------------------------------------------ #
    def replace_document_judge_results(self, rows: list[dict | JudgeResult], backend: str) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute("DELETE FROM document_judge_results")
            for r in rows:
                if isinstance(r, JudgeResult):
                    item_id = r.item_id
                    item_label = r.item_label
                    status = r.status
                    summary = r.summary
                    covered = json.dumps(r.covered_files)
                else:
                    item_id = r["item_id"]
                    item_label = r["item_label"]
                    status = r["status"]
                    summary = r.get("summary", "")
                    covered = json.dumps(r.get("covered_files", []))

                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO document_judge_results
                    (item_id, item_label, status, summary, covered_files, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (item_id, item_label, status, summary, covered, now),
                )
            self._set_run_metadata("document_judge", backend, now)

    def get_document_judge_results(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM document_judge_results ORDER BY item_label")
        return self._unpack_covered_files(cursor.fetchall())

    # ------------------------------------------------------------------ #
    # Test-chain results (`llm-judge`)
    # ------------------------------------------------------------------ #
    def replace_test_chain_results(self, rows: list[dict | TestChainResult], backend: str) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute("DELETE FROM test_chain_results")
            for r in rows:
                if isinstance(r, TestChainResult):
                    comp_name = r.component_name
                    status = r.status
                    summary = r.summary
                else:
                    comp_name = r["component_name"]
                    status = r["status"]
                    summary = r.get("summary", "")

                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO test_chain_results
                    (component_name, status, summary, generated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (comp_name, status, summary, now),
                )
            self._set_run_metadata("test_chain", backend, now)

    def get_test_chain_results(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM test_chain_results ORDER BY component_name")
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------ #
    # Run provenance & staleness tracking
    # ------------------------------------------------------------------ #
    def get_run_metadata(self, run_type: str) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM run_metadata WHERE run_type = ?", (run_type,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def set_assessed_doc_hashes(self, run_type: str, doc_hashes: dict[str, str]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM assessed_doc_hashes WHERE run_type = ?", (run_type,))
            for file_path, content_hash in doc_hashes.items():
                self.conn.execute(
                    "INSERT OR REPLACE INTO assessed_doc_hashes (run_type, file_path, content_hash) "
                    "VALUES (?, ?, ?)",
                    (run_type, file_path, content_hash),
                )

    def get_assessed_doc_hashes(self, run_type: str) -> dict[str, str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT file_path, content_hash FROM assessed_doc_hashes WHERE run_type = ?",
            (run_type,),
        )
        return {row["file_path"]: row["content_hash"] for row in cursor.fetchall()}

    # ------------------------------------------------------------------ #
    # Consistency Baseline Persistence
    # ------------------------------------------------------------------ #
    def replace_consistency_baseline(self, baseline: dict) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO consistency_baseline (id, version, data_json, updated_at)
                VALUES (1, ?, ?, ?)
                """,
                (baseline.get("version", 2), json.dumps(baseline), now),
            )

    def get_consistency_baseline(self) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT data_json FROM consistency_baseline WHERE id = 1")
        row = cursor.fetchone()
        if row and row["data_json"]:
            return json.loads(row["data_json"])
        return None

    def close(self):
        self.conn.close()


__all__ = [
    "ConsistencySummary",
    "DocAuditDB",
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
