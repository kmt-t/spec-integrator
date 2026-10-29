from __future__ import annotations

import sqlite3


class DatabaseSchema:
    conn: sqlite3.Connection

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
            # Derived identifier definitions and references. These tables are fully
            # replaced from source files on every workspace load.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS identifiers (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    family TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    normalized_id TEXT NOT NULL,
                    definition_kind TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    section_id TEXT,
                    line INTEGER NOT NULL,
                    column_number INTEGER NOT NULL,
                    source_hash TEXT NOT NULL
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS identifier_references (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    family TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    reference_kind TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    section_id TEXT,
                    line INTEGER NOT NULL,
                    column_number INTEGER NOT NULL,
                    range_end TEXT,
                    range_notation TEXT,
                    expanded_ids TEXT NOT NULL DEFAULT '[]'
                )
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_identifiers_lookup
                ON identifiers (family, normalized_id)
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_identifier_references_lookup
                ON identifier_references (family, identifier)
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
            # 12a. Per-criterion LLM evaluations, including confidence values.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS judge_evaluations (
                    run_type TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    item_label TEXT NOT NULL,
                    check_id TEXT NOT NULL,
                    location TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    severity TEXT,
                    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
                    covered_files TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    PRIMARY KEY (run_type, item_id, check_id, location, backend)
                )
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_judge_evaluations_confidence
                ON judge_evaluations (confidence DESC)
            """)
            # 13. run_metadata
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
                    backend TEXT NOT NULL DEFAULT 'jev',
                    UNIQUE(term_a, term_b, file_a, file_b, backend)
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

    def _migrate_term_variance_backend_key(self) -> None:
        """Preserve per-backend term judgments when upgrading legacy databases."""
        table = "term_variance_judgments"
        columns = {
            str(row["name"]) for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "backend" not in columns:
            with self.conn:
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN backend TEXT NOT NULL DEFAULT 'jev'"
                )

        unique_column_sets: set[frozenset[str]] = set()
        indexes = self.conn.execute(f"PRAGMA index_list({table})").fetchall()
        for index in indexes:
            if not index["unique"]:
                continue
            index_name = str(index["name"])
            index_columns = self.conn.execute(f"PRAGMA index_info('{index_name}')").fetchall()
            unique_column_sets.add(frozenset(str(column["name"]) for column in index_columns))

        backend_key = frozenset({"term_a", "term_b", "file_a", "file_b", "backend"})
        legacy_key = frozenset({"term_a", "term_b", "file_a", "file_b"})
        if backend_key in unique_column_sets and legacy_key not in unique_column_sets:
            return

        with self.conn:
            self.conn.execute("""
                CREATE TABLE term_variance_judgments_backend (
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
                    backend TEXT NOT NULL DEFAULT 'jev',
                    UNIQUE(term_a, term_b, file_a, file_b, backend)
                )
            """)
            self.conn.execute(f"""
                INSERT INTO term_variance_judgments_backend
                    (id, term_a, term_b, file_a, file_b, line_a, line_b, is_variance,
                     confidence, preferred_term, reason, judged_at, backend)
                SELECT id, term_a, term_b, file_a, file_b, line_a, line_b, is_variance,
                       confidence, preferred_term, reason, judged_at, COALESCE(backend, 'jev')
                FROM {table}
            """)
            self.conn.execute(f"DROP TABLE {table}")
            self.conn.execute(f"ALTER TABLE term_variance_judgments_backend RENAME TO {table}")
