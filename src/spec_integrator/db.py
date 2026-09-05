from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

from spec_integrator.models import (
    ConsistencySummary,
    FormalModelResult,
    JudgeReport,
    JudgeResult,
    KeywordRiskAssessment,
    ObligationSummary,
    ParsedDocument,
    ParsedLink,
    ParsedSection,
    PropertyResult,
    RiskAssessmentReport,
    SymbolDrift,
    TestChainReport,
    TestChainResult,
    TestChainTarget,
    VerificationIssue,
    WITFileResult,
)


# ===========================================================================
# Database Persistence Layer (SQLite DAO)
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
            # 16. term_keywords
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS term_keywords (
                    term TEXT PRIMARY KEY,
                    category TEXT,
                    df INTEGER,
                    total_occurrences INTEGER,
                    tf_idf_score REAL,
                    occurrences_json TEXT,
                    updated_at TEXT
                )
            """)
            # 18. term_embeddings
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS term_embeddings (
                    term TEXT,
                    embedding_json TEXT,
                    model TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (term, model)
                )
            """)
            # 19. term_similarities
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS term_similarities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term_a TEXT,
                    term_b TEXT,
                    similarity REAL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    UNIQUE(term_a, term_b)
                )
            """)
            # 20. term_variance_judgments
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS term_variance_judgments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term_a TEXT,
                    term_b TEXT,
                    file_a TEXT,
                    file_b TEXT,
                    line_a INTEGER,
                    line_b INTEGER,
                    is_variance INTEGER,
                    confidence REAL,
                    preferred_term TEXT,
                    reason TEXT,
                    judged_at TEXT,
                    backend TEXT,
                    UNIQUE(term_a, term_b, file_a, file_b)
                )
            """)
            # 21. section_embeddings
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS section_embeddings (
                    section_id TEXT,
                    file_path TEXT,
                    heading TEXT,
                    content_hash TEXT,
                    embedding_json TEXT,
                    model TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (section_id, model)
                )
            """)
            # 22. section_similarities
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS section_similarities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section_a TEXT,
                    section_b TEXT,
                    file_a TEXT,
                    file_b TEXT,
                    similarity REAL,
                    model TEXT,
                    created_at TEXT,
                    UNIQUE(section_a, section_b, model)
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

    # ---------------------------------------------------------------------------
    # Terminology & Variance Persistence
    # ---------------------------------------------------------------------------
    def replace_term_keywords(self, terms: list[dict]) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute("DELETE FROM term_keywords")
            self.conn.executemany(
                """
                INSERT INTO term_keywords
                (term, category, df, total_occurrences, tf_idf_score, occurrences_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        t["term"],
                        t.get("category", "general"),
                        t.get("df", 1),
                        t.get("total_occurrences", 1),
                        t.get("tf_idf_score", 0.0),
                        json.dumps(t.get("occurrences", []), ensure_ascii=False),
                        now,
                    )
                    for t in terms
                ],
            )

    def get_all_term_keywords(self) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM term_keywords ORDER BY tf_idf_score DESC")
        return cursor.fetchall()

    def get_term_keyword(self, term: str) -> sqlite3.Row | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM term_keywords WHERE term = ?", (term,))
        return cursor.fetchone()

    def get_unembedded_terms(self, model: str) -> list[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT tk.term FROM term_keywords tk
            LEFT JOIN term_embeddings te ON tk.term = te.term AND te.model = ?
            WHERE te.term IS NULL
            ORDER BY tk.tf_idf_score DESC
            """,
            (model,),
        )
        return [row["term"] for row in cursor.fetchall()]

    def insert_term_embeddings(self, embeddings: list[tuple[str, list[float], str]]) -> None:
        now = self._now()
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO term_embeddings (term, embedding_json, model, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                [(term, json.dumps(vec), model, now) for term, vec, model in embeddings],
            )

    def get_all_term_embeddings(self, model: str) -> dict[str, list[float]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT term, embedding_json FROM term_embeddings WHERE model = ?", (model,))
        result = {}
        for row in cursor.fetchall():
            try:
                result[row["term"]] = json.loads(row["embedding_json"])
            except Exception:
                pass
        return result

    def replace_term_similarities(self, similarities: list[tuple[str, str, float]]) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute("DELETE FROM term_similarities")
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO term_similarities (term_a, term_b, similarity, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                [(a, b, sim, now) for a, b, sim in similarities],
            )

    def get_term_similarities(self, min_similarity: float = 0.0) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM term_similarities WHERE similarity >= ? ORDER BY similarity DESC",
            (min_similarity,),
        )
        return cursor.fetchall()

    def insert_term_variance_judgment(
        self,
        term_a: str,
        term_b: str,
        file_a: str,
        file_b: str,
        line_a: int,
        line_b: int,
        is_variance: bool,
        confidence: float,
        preferred_term: str,
        reason: str,
        backend: str,
    ) -> None:
        now = self._now()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO term_variance_judgments
                (term_a, term_b, file_a, file_b, line_a, line_b, is_variance, confidence, preferred_term, reason, judged_at, backend)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    term_a,
                    term_b,
                    file_a,
                    file_b,
                    line_a,
                    line_b,
                    1 if is_variance else 0,
                    confidence,
                    preferred_term,
                    reason,
                    now,
                    backend,
                ),
            )
            # Mark similarity status as judged
            self.conn.execute(
                """
                UPDATE term_similarities SET status = 'judged'
                WHERE (term_a = ? AND term_b = ?) OR (term_a = ? AND term_b = ?)
                """,
                (term_a, term_b, term_b, term_a),
            )

    def is_similarity_judged(self, term_a: str, term_b: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM term_variance_judgments
            WHERE (term_a = ? AND term_b = ?) OR (term_a = ? AND term_b = ?)
            LIMIT 1
            """,
            (term_a, term_b, term_b, term_a),
        )
        return cursor.fetchone() is not None

    def get_high_confidence_variances(self, min_confidence: float = 0.70) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM term_variance_judgments
            WHERE is_variance = 1 AND confidence >= ?
            ORDER BY confidence DESC
            """,
            (min_confidence,),
        )
        return cursor.fetchall()

    def get_all_term_variance_judgments(self) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM term_variance_judgments ORDER BY confidence DESC")
        return cursor.fetchall()

    def get_unembedded_sections(self, model: str) -> list[tuple[str, str, str, str, str]]:
        """Returns sections needing embeddings: (section_id, file_path, heading, body_text, content_hash)"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT s.section_id, s.file_path, s.heading, s.body_text, s.content_hash
            FROM sections s
            LEFT JOIN section_embeddings se ON s.section_id = se.section_id AND se.model = ?
            WHERE se.section_id IS NULL OR se.content_hash != s.content_hash
            """,
            (model,),
        )
        return [
            (r["section_id"], r["file_path"], r["heading"], r["body_text"], r["content_hash"])
            for r in cursor.fetchall()
        ]

    def insert_section_embeddings(
        self, embeddings: list[tuple[str, str, str, str, list[float], str]]
    ) -> None:
        """(section_id, file_path, heading, content_hash, vector, model)"""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rows = [
            (sec_id, fp, hd, ch, json.dumps(vec), model, now)
            for sec_id, fp, hd, ch, vec, model in embeddings
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO section_embeddings
                (section_id, file_path, heading, content_hash, embedding_json, model, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_all_section_embeddings(self, model: str) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT se.section_id, se.file_path, se.heading, s.line_start, se.embedding_json
            FROM section_embeddings se
            JOIN sections s ON se.section_id = s.section_id
            WHERE se.model = ?
            """,
            (model,),
        )
        results = []
        for r in cursor.fetchall():
            results.append(
                {
                    "section_id": r["section_id"],
                    "file_path": r["file_path"],
                    "heading": r["heading"],
                    "line_start": r["line_start"],
                    "vector": json.loads(r["embedding_json"]),
                }
            )
        return results

    def replace_section_similarities(
        self, similarities: list[tuple[str, str, str, str, float, str]]
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rows = [(a, b, fa, fb, sim, model, now) for a, b, fa, fb, sim, model in similarities]
        with self.conn:
            self.conn.execute("DELETE FROM section_similarities")
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO section_similarities
                (section_a, section_b, file_a, file_b, similarity, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_section_similarities(
        self, model: str | None = None, min_similarity: float = 0.80
    ) -> list[dict]:
        cursor = self.conn.cursor()
        if model:
            cursor.execute(
                """
                SELECT section_a, section_b, file_a, file_b, similarity, model
                FROM section_similarities
                WHERE model = ? AND similarity >= ?
                ORDER BY similarity DESC
                """,
                (model, min_similarity),
            )
        else:
            cursor.execute(
                """
                SELECT section_a, section_b, file_a, file_b, similarity, model
                FROM section_similarities
                WHERE similarity >= ?
                ORDER BY similarity DESC
                """,
                (min_similarity,),
            )
        return [dict(r) for r in cursor.fetchall()]

    def get_section_keywords_map(self) -> dict[str, set[str]]:
        """Returns {section_id: set_of_keywords} for all sections."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT section_id, keyword FROM keyword_references WHERE section_id IS NOT NULL"
        )
        mapping: dict[str, set[str]] = {}
        for r in cursor.fetchall():
            mapping.setdefault(r["section_id"], set()).add(r["keyword"])
        return mapping

    def get_section_info(self, section_id: str) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT section_id, file_path, heading, line_start, line_end, body_text
            FROM sections WHERE section_id = ?
            """,
            (section_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

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
