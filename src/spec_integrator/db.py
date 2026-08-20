from __future__ import annotations

import sqlite3
import hashlib
import datetime
from pathlib import Path
from dataclasses import dataclass, asdict


class DocAuditDB:
    def __init__(self, db_path: Path | str = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        # Performance tuning for network/local filesystems
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
            # 7. audit_cache
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

    @staticmethod
    def compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def clear_all(self):
        with self.conn:
            self.conn.execute("DELETE FROM keyword_references")
            self.conn.execute("DELETE FROM document_links")
            self.conn.execute("DELETE FROM sections")
            self.conn.execute("DELETE FROM documents")
            self.conn.execute("DELETE FROM keywords")
            self.conn.execute("DELETE FROM formal_models")

    def insert_document(self, file_path: str, tier: str, component: str, content_hash: str):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.conn.execute("""
            INSERT OR REPLACE INTO documents (file_path, tier, component, content_hash, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (file_path, str(tier) if tier is not None else None, component, content_hash, now))

    def insert_section(self, section_id: str, file_path: str, heading: str, level: int,
                       line_start: int, line_end: int, body_text: str, content_hash: str):
        self.conn.execute("""
            INSERT OR REPLACE INTO sections 
            (section_id, file_path, heading, level, line_start, line_end, body_text, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (section_id, file_path, heading, level, line_start, line_end, body_text, content_hash))

    def insert_keyword(self, keyword: str, category: str, defined_in_file: str, defined_in_section: str, description: str = ""):
        self.conn.execute("""
            INSERT OR REPLACE INTO keywords (keyword, category, defined_in_file, defined_in_section, description)
            VALUES (?, ?, ?, ?, ?)
        """, (keyword, category, defined_in_file, defined_in_section, description))

    def insert_keyword_reference(self, keyword: str, file_path: str, section_id: str,
                                 relation_type: str, line_number: int):
        self.conn.execute("""
            INSERT INTO keyword_references (keyword, file_path, section_id, relation_type, line_number)
            VALUES (?, ?, ?, ?, ?)
        """, (keyword, file_path, section_id, relation_type, line_number))

    def insert_link(self, source_file: str, source_line: int, target_path: str, target_anchor: str, is_valid: int):
        self.conn.execute("""
            INSERT INTO document_links (source_file, source_line, target_path, target_anchor, is_valid)
            VALUES (?, ?, ?, ?, ?)
        """, (source_file, source_line, target_path, target_anchor, is_valid))

    def insert_formal_model(self, component: str, model_path: str, framework: str, status: str, details: str = ""):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.conn.execute("""
            INSERT OR REPLACE INTO formal_models (component, model_path, framework, status, details, checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (component, model_path, framework, status, details, now))

    def commit(self):
        self.conn.commit()

    def get_all_documents(self) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM documents ORDER BY file_path")
        return cursor.fetchall()

    def get_all_sections(self) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM sections ORDER BY file_path, line_start")
        return cursor.fetchall()

    def get_all_keywords(self) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM keywords ORDER BY keyword")
        return cursor.fetchall()

    def get_keyword_references(self, keyword: str | None = None) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        if keyword:
            cursor.execute("SELECT * FROM keyword_references WHERE keyword = ? ORDER BY file_path, line_number", (keyword,))
        else:
            cursor.execute("SELECT * FROM keyword_references ORDER BY keyword, file_path, line_number")
        return cursor.fetchall()

    def get_invalid_links(self) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM document_links WHERE is_valid = 0 ORDER BY source_file, source_line")
        return cursor.fetchall()

    def get_cache(self, hash_key: str) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT status, reason FROM audit_cache WHERE hash_key = ?", (hash_key,))
        row = cursor.fetchone()
        if row:
            return {"status": row["status"], "reason": row["reason"]}
        return None

    def set_cache(self, hash_key: str, rule_code: str, target_id: str, status: str, reason: str):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO audit_cache (hash_key, rule_code, target_id, status, reason, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (hash_key, rule_code, target_id, status, reason, now))

    def close(self):
        self.conn.close()
