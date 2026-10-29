from __future__ import annotations

import json
import sqlite3

from spec_integrator.models import (
    VerificationIssue,
)


class DatabaseQueries:
    conn: sqlite3.Connection

    def get_all_documents(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM documents ORDER BY file_path")
        return [dict(row) for row in cursor.fetchall()]

    def get_all_sections(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM sections ORDER BY file_path, line_start")
        return [dict(row) for row in cursor.fetchall()]

    def get_identifiers(self) -> list[dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM identifiers ORDER BY family, identifier, file_path, line")
        return [dict(row) for row in cursor.fetchall()]

    def get_identifier_references(self) -> list[dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM identifier_references ORDER BY family, identifier, file_path, line"
        )
        result: list[dict[str, object]] = []
        for row in cursor.fetchall():
            item = dict(row)
            item["expanded_ids"] = json.loads(str(item.get("expanded_ids") or "[]"))
            result.append(item)
        return result

    def get_keyword_references(self, keyword: str) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM keyword_references WHERE keyword = ?", (keyword,))
        return [dict(row) for row in cursor.fetchall()]

    def get_invalid_links(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM document_links WHERE is_valid = 0")
        return [dict(row) for row in cursor.fetchall()]

    def get_cache(self, hash_key: str) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM audit_cache WHERE hash_key = ?", (hash_key,))
        row = cursor.fetchone()
        return dict(row) if row else None

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

    def get_risk_assessments(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM risk_assessments ORDER BY risk_score DESC")
        return self._unpack_covered_files(cursor.fetchall())

    def get_judge_results(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM judge_results ORDER BY item_label")
        return self._unpack_covered_files(cursor.fetchall())

    def get_document_judge_results(self) -> list[dict]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM document_judge_results ORDER BY item_label")
        return self._unpack_covered_files(cursor.fetchall())

    def get_judge_evaluations(
        self,
        min_confidence: float = 0.70,
        classifications: list[str] | None = None,
        run_type: str | None = None,
        limit: int | None = 200,
        backend: str | None = None,
    ) -> list[dict]:
        """Queries persisted criterion decisions by score, backend, and outcome."""
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0.0 and 1.0")
        clauses = ["confidence >= ?"]
        parameters: list[str | float | int] = [min_confidence]
        if classifications:
            placeholders = ", ".join("?" for _ in classifications)
            clauses.append(f"classification IN ({placeholders})")
            parameters.extend(classifications)
        if run_type:
            clauses.append("run_type = ?")
            parameters.append(run_type)
        if backend:
            clauses.append("backend = ?")
            parameters.append(backend)
        query = (
            "SELECT * FROM judge_evaluations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY confidence DESC, run_type, item_label, check_id"
        )
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            parameters.append(limit)
        cursor = self.conn.cursor()
        cursor.execute(query, parameters)
        rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["covered_files"] = json.loads(row["covered_files"] or "[]")
        return rows

    def get_run_metadata(self, run_type: str) -> dict | None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM run_metadata WHERE run_type = ?", (run_type,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_assessed_doc_hashes(self, run_type: str) -> dict[str, str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT file_path, content_hash FROM assessed_doc_hashes WHERE run_type = ?",
            (run_type,),
        )
        return {row["file_path"]: row["content_hash"] for row in cursor.fetchall()}

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

    def get_term_similarities(self, min_similarity: float = 0.0) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM term_similarities WHERE similarity >= ? ORDER BY similarity DESC",
            (min_similarity,),
        )
        return cursor.fetchall()

    def is_similarity_judged(self, term_a: str, term_b: str, backend: str | None = None) -> bool:
        cursor = self.conn.cursor()
        backend_filter = " AND backend = ?" if backend else ""
        parameters: tuple[str, ...] = (term_a, term_b, term_b, term_a)
        if backend:
            parameters += (backend,)
        cursor.execute(
            f"""
            SELECT 1 FROM term_variance_judgments
            WHERE ((term_a = ? AND term_b = ?) OR (term_a = ? AND term_b = ?))
            {backend_filter}
            LIMIT 1
            """,
            parameters,
        )
        return cursor.fetchone() is not None

    def get_high_confidence_variances(
        self, min_confidence: float = 0.70, backend: str | None = None
    ) -> list[sqlite3.Row]:
        cursor = self.conn.cursor()
        backend_filter = " AND backend = ?" if backend else ""
        parameters: tuple[float | str, ...] = (min_confidence,)
        if backend:
            parameters += (backend,)
        cursor.execute(
            f"""
            SELECT * FROM term_variance_judgments
            WHERE is_variance = 1 AND confidence >= ?
            {backend_filter}
            ORDER BY confidence DESC
            """,
            parameters,
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
