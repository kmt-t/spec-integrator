"""SQLite database connection and public persistence interface."""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from spec_integrator.db.query import DatabaseQueries
from spec_integrator.db.save import DatabaseWrites
from spec_integrator.db.schema import DatabaseSchema


class DocAuditDB(DatabaseSchema, DatabaseWrites, DatabaseQueries):
    """Unified SQLite persistence for document, verification, and LLM results."""

    conn: sqlite3.Connection
    db_path: str

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
        self._migrate_term_variance_backend_key()

    def _now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def close(self):
        self.conn.close()
