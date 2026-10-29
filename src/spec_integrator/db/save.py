from __future__ import annotations

import datetime
import json
import sqlite3

from spec_integrator.models import (
    IdentifierDefinition,
    IdentifierReference,
    JudgeResult,
    KeywordRiskAssessment,
    VerificationIssue,
)


class DatabaseWrites:
    conn: sqlite3.Connection

    def clear_all(self):
        """Clears every application table, including cached assessments and indexes."""
        tables = [
            str(row["name"])
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        with self.conn:
            for table in tables:
                quoted_table = table.replace('"', '""')
                self.conn.execute(f'DELETE FROM "{quoted_table}"')

    def commit(self):
        self.conn.commit()

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

    def replace_identifier_index(
        self,
        definitions: list[IdentifierDefinition],
        references: list[IdentifierReference],
    ) -> None:
        """Atomically replace the derived identifier index from a full scan."""
        with self.conn:
            self.conn.execute("DELETE FROM identifier_references")
            self.conn.execute("DELETE FROM identifiers")
            self.conn.executemany(
                """
                INSERT INTO identifiers
                (family, identifier, normalized_id, definition_kind, file_path,
                 section_id, line, column_number, source_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.family,
                        item.identifier,
                        item.normalized_id,
                        item.definition_kind,
                        item.file_path,
                        item.section_id,
                        item.line,
                        item.column_number,
                        item.source_hash,
                    )
                    for item in definitions
                ],
            )
            self.conn.executemany(
                """
                INSERT INTO identifier_references
                (family, identifier, raw_text, reference_kind, file_path, section_id,
                 line, column_number, range_end, range_notation, expanded_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.family,
                        item.identifier,
                        item.raw_text,
                        item.reference_kind,
                        item.file_path,
                        item.section_id,
                        item.line,
                        item.column_number,
                        item.range_end,
                        item.range_notation,
                        json.dumps(item.expanded_ids),
                    )
                    for item in references
                ],
            )

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

    def _set_run_metadata(self, run_type: str, backend: str, now: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO run_metadata (run_type, backend, generated_at)
            VALUES (?, ?, ?)
            """,
            (run_type, backend, now),
        )

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

    def save_judge_evaluations(
        self,
        run_type: str,
        rows: list[JudgeResult],
        backend: str,
        replace_all: bool = False,
        replace_keywords: list[str] | None = None,
    ) -> None:
        """Stores each typed criterion decision with its backend-specific score."""
        now = self._now()
        with self.conn:
            if replace_all:
                self.conn.execute(
                    "DELETE FROM judge_evaluations WHERE run_type = ? AND backend = ?",
                    (run_type, backend),
                )
            elif replace_keywords:
                for keyword in replace_keywords:
                    pair_label_prefix = f"{{{keyword}}} | "
                    self.conn.execute(
                        "DELETE FROM judge_evaluations "
                        "WHERE run_type = ? AND backend = ? "
                        "AND substr(item_label, 1, length(?)) = ?",
                        (run_type, backend, pair_label_prefix, pair_label_prefix),
                    )
            for result in rows:
                if not result.evaluations:
                    continue
                self.conn.execute(
                    "DELETE FROM judge_evaluations "
                    "WHERE run_type = ? AND item_id = ? AND backend = ?",
                    (run_type, result.item_id, backend),
                )
                covered = json.dumps(result.covered_files)
                for evaluation in result.evaluations:
                    self.conn.execute(
                        """
                        INSERT OR REPLACE INTO judge_evaluations
                        (run_type, item_id, item_label, check_id, location, classification,
                         severity, confidence, covered_files, generated_at, backend)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_type,
                            result.item_id,
                            result.item_label,
                            evaluation.check_id,
                            evaluation.location,
                            evaluation.classification,
                            evaluation.severity,
                            evaluation.confidence,
                            covered,
                            now,
                            backend,
                        ),
                    )
            self._set_run_metadata(run_type, backend, now)

    def set_assessed_doc_hashes(self, run_type: str, doc_hashes: dict[str, str]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM assessed_doc_hashes WHERE run_type = ?", (run_type,))
            for file_path, content_hash in doc_hashes.items():
                self.conn.execute(
                    "INSERT OR REPLACE INTO assessed_doc_hashes (run_type, file_path, content_hash) "
                    "VALUES (?, ?, ?)",
                    (run_type, file_path, content_hash),
                )

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
