from __future__ import annotations

from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.graph import Graph
from spec_integrator.models import ObligationSummary, ParsedDocument, VerificationIssue

__all__ = ["ObligationSummary", "ObligationVerifier"]


class ObligationVerifier:
    """Obligation Gate.
    The risk assessment (`llm-assess`) decides *what must be verified*. This gate
    closes the loop: an unmet verification obligation is an ERROR. Assessments,
    judge verdicts, provenance, and staleness hashes are all read from the cache DB.
    """

    def __init__(self, config: Config):
        self.config = config

    def verify(
        self,
        documents: list[ParsedDocument],
        graph: Graph | None = None,
        db: DocAuditDB | None = None,
    ) -> tuple[list[VerificationIssue], ObligationSummary]:
        summary = ObligationSummary()
        cfg = self.config.obligation
        if not cfg.enabled:
            return [], summary
        issues: list[VerificationIssue] = []
        if db is None:
            if cfg.require_assessment:
                issues.append(
                    VerificationIssue(
                        gate="Obligation",
                        severity="ERROR",
                        file_path=str(self.config.get_db_path()),
                        line=1,
                        rule_code="OBLIG-ASSESSMENT-MISSING",
                        message=(
                            "No cache DB available, so no risk assessment can be read. The "
                            "pipeline cannot claim the specification is verified without first "
                            "deciding what needs verifying. Run 'spec-integrator llm-assess' "
                            "before 'check'."
                        ),
                    )
                )
            return issues, summary

        assessments = db.get_risk_assessments()
        doc_hashes = db.get_assessed_doc_hashes("risk_assessment")
        doc_map = {d.file_path: d for d in documents}

        if not assessments and not doc_hashes:
            if cfg.require_assessment:
                issues.append(
                    VerificationIssue(
                        gate="Obligation",
                        severity="ERROR",
                        file_path=str(self.config.get_db_path()),
                        line=1,
                        rule_code="OBLIG-ASSESSMENT-MISSING",
                        message=(
                            "No risk assessment found in the cache DB. The pipeline cannot "
                            "claim the specification is verified without first deciding what "
                            "needs verifying. Run 'spec-integrator llm-assess' before 'check'."
                        ),
                    )
                )
            return issues, summary

        # Coverage must be measured against the keywords that exist now, not against
        # the ones the assessment happened to look at. "13/13 discharged" is not a
        # clean bill of health when the obligations were derived from 15 of 663
        # keywords — the other 648 have unknown obligations, not zero.
        # A mock assessor infers "what must be verified" from the tags the document
        # already carries. The Obligation Gate then checks those same tags, so the
        # discharge rate is 1.0 by construction, whatever the specification says.
        run_meta = db.get_run_metadata("risk_assessment")
        backend = str((run_meta or {}).get("backend") or "").lower()
        if not backend:
            issues.append(
                VerificationIssue(
                    gate="Obligation",
                    severity="ERROR",
                    file_path=str(self.config.get_db_path()),
                    line=1,
                    rule_code="OBLIG-ASSESSMENT-PROVENANCE-UNKNOWN",
                    message=(
                        "The risk assessment records no backend, so its independence from the "
                        "documents it judges cannot be established. Re-run 'llm-assess' with a "
                        "tool version that stamps the engine."
                    ),
                )
            )
        elif backend in cfg.forbidden_backends:
            issues.append(
                VerificationIssue(
                    gate="Obligation",
                    severity="ERROR",
                    file_path=str(self.config.get_db_path()),
                    line=1,
                    rule_code="OBLIG-ASSESSMENT-NOT-INDEPENDENT",
                    message=(
                        f"The risk assessment was produced by the '{backend}' backend, which "
                        "derives each obligation from the tags the document already carries. "
                        "The discharge rate is then true by construction and says nothing about "
                        "the specification. Re-run 'llm-assess' against a real backend."
                    ),
                )
            )

        # Coverage must be measured against the keywords that exist now (the same
        # population `llm-judge` audits), not against however many the assessment
        # happened to look at -- graph is None only in callers/tests that don't
        # care about this specific check, in which case it is skipped rather than
        # guessed at.
        summary.keywords_assessed = len(assessments)
        if graph is not None:
            summary.keywords_total = len(graph.extract_item_subgraphs())
            if cfg.require_full_coverage and summary.keywords_assessed < summary.keywords_total:
                issues.append(
                    VerificationIssue(
                        gate="Obligation",
                        severity="ERROR",
                        file_path=str(self.config.get_db_path()),
                        line=1,
                        rule_code="OBLIG-ASSESSMENT-PARTIAL",
                        message=(
                            f"Only {summary.keywords_assessed} of {summary.keywords_total} "
                            "keyword(s) were risk-assessed, so the verification obligations of "
                            "the remainder are unknown. A discharge rate computed over a partial "
                            "assessment does not mean the specification is covered. Re-run "
                            "'llm-assess --exhaustive'."
                        ),
                    )
                )

        # --- 1. Staleness: the assessment must describe the documents as they are now ---
        assessed_files = {a["file_path"] for a in assessments if a.get("file_path")}
        summary.assessed_documents = len(assessed_files)
        for doc in documents:
            recorded = doc_hashes.get(doc.file_path)
            if recorded is None:
                if doc.file_path in assessed_files:
                    continue  # assessed by an older tool version that stored no hash
                summary.unassessed_documents.append(doc.file_path)
                continue
            if recorded != doc.content_hash:
                summary.stale_documents.append(doc.file_path)
                if cfg.stale_is_error:
                    issues.append(
                        VerificationIssue(
                            gate="Obligation",
                            severity="ERROR",
                            file_path=doc.file_path,
                            line=1,
                            rule_code="OBLIG-ASSESSMENT-STALE",
                            message=(
                                "Document changed since it was risk-assessed. The recorded "
                                "verification obligations no longer describe this content — "
                                "re-run 'spec-integrator llm-assess'."
                            ),
                        )
                    )

        # --- 2. A high-risk keyword must actually be tagged for semantic audit ---
        # Assessment scores complexity/risk only -- it does not route to a
        # verification method. A high risk_score demands exactly one thing:
        # `{VERIFY_LLM}`, present on some document in the keyword's own
        # definition/reference subgraph (matching how `llm-judge` itself
        # decides what to prioritize). Whether formal verification is ALSO
        # warranted is a deliberate authorial decision, not an auto-demand.
        llm_tag = self.config.llm_judge.tag
        for a in assessments:
            keyword = a.get("keyword", "")
            file_path = a.get("file_path", "")
            risk = int(a.get("risk_score", 0) or 0)
            covered_files = list(a.get("covered_files") or ([file_path] if file_path else []))
            line = int(a.get("line", 1) or 1)

            if risk < cfg.risk_threshold or not covered_files:
                continue
            summary.demanded += 1
            present = any(llm_tag in doc_map[f].all_tags for f in covered_files if f in doc_map)
            if present:
                summary.discharged += 1
                continue
            summary.skipped.append(
                {
                    "keyword": keyword,
                    "file_path": file_path,
                    "risk_score": risk,
                    "missing_tags": [llm_tag],
                }
            )
            issues.append(
                VerificationIssue(
                    gate="Obligation",
                    severity="ERROR",
                    file_path=file_path or covered_files[0],
                    line=line,
                    rule_code="OBLIG-VERIFICATION-SKIPPED",
                    message=(
                        f"'{{{keyword}}}' was assessed at risk {risk}/5 and requires {llm_tag}, "
                        "but none of its defining/referencing documents carry that tag, so the "
                        "verification is never executed. Add the tag and supply the evidence, "
                        "or record an explicit, justified waiver."
                    ),
                )
            )

        # --- 3. {VERIFY_LLM} must have actually been judged ---
        if cfg.require_judge:
            issues.extend(self._verify_judge_coverage(documents, summary, db))
        return issues, summary

    # ------------------------------------------------------------------ #
    def _verify_judge_coverage(
        self, documents: list[ParsedDocument], summary: ObligationSummary, db: DocAuditDB
    ) -> list[VerificationIssue]:
        llm_tag = self.config.llm_judge.tag
        tagged = [d for d in documents if llm_tag in d.all_tags]
        if not tagged:
            return []

        issues: list[VerificationIssue] = []

        # --- 1. Keyword subgraph audit: definition + referencing sections ---
        entries, covered = self._check_llm_run_coverage(
            tagged,
            db.get_judge_results(),
            db.get_assessed_doc_hashes("judge"),
            run_label="LLM judge",
            rule_prefix="OBLIG-JUDGE",
            command="'spec-integrator llm-judge'",
            skipped_hint="Raise --max-subgraphs so the audit actually covers it.",
            issues=issues,
            missing_out=summary.judge_missing,
        )
        failed_keywords = {
            e.get("item_label", "").strip("{}") for e in entries if e.get("status") == "FAIL"
        }
        for doc in tagged:
            if doc.file_path not in covered:
                continue  # already reported as OBLIG-JUDGE-SKIPPED above
            for kw in sorted(failed_keywords & set(doc.all_keywords)):
                issues.append(
                    VerificationIssue(
                        gate="Obligation",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=1,
                        rule_code="OBLIG-JUDGE-FAILED",
                        message=(
                            f"LLM semantic audit reported FAIL for '{{{kw}}}', which this document "
                            "cites, in the stored judge verdict."
                        ),
                    )
                )

        # --- 2. Whole-document self-consistency audit ---
        # A document can be covered above through only one small keyword
        # subgraph while the bulk of its own prose was never actually judged;
        # this is the independent check that closes that gap.
        doc_entries, doc_covered = self._check_llm_run_coverage(
            tagged,
            db.get_document_judge_results(),
            db.get_assessed_doc_hashes("document_judge"),
            run_label="whole-document LLM judge",
            rule_prefix="OBLIG-DOC-JUDGE",
            command="'spec-integrator llm-judge'",
            skipped_hint="Raise --max-documents so the audit actually covers it.",
            issues=issues,
            missing_out=summary.document_judge_missing,
        )
        failed_docs = {e["item_id"] for e in doc_entries if e.get("status") == "FAIL"}
        for doc in tagged:
            if doc.file_path in doc_covered and doc.file_path in failed_docs:
                issues.append(
                    VerificationIssue(
                        gate="Obligation",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=1,
                        rule_code="OBLIG-DOC-JUDGE-FAILED",
                        message=(
                            "The whole-document LLM semantic audit reported FAIL for this "
                            "document in the stored judge verdict."
                        ),
                    )
                )

        return issues

    def _check_llm_run_coverage(
        self,
        tagged: list[ParsedDocument],
        entries: list[dict],
        hashes: dict[str, str],
        *,
        run_label: str,
        rule_prefix: str,
        command: str,
        skipped_hint: str,
        issues: list[VerificationIssue],
        missing_out: list[str],
    ) -> tuple[list[dict], set[str]]:
        """Shared MISSING/UNANCHORED/STALE/SKIPPED check for one LLM audit
        table against the documents that declare `{VERIFY_LLM}`. Appends any
        issues found directly to `issues` and returns (entries, covered_files)
        so the caller can layer its own FAIL-specific check on top."""
        llm_tag = self.config.llm_judge.tag
        if not entries and not hashes:
            missing_out.extend(d.file_path for d in tagged)
            issues.append(
                VerificationIssue(
                    gate="Obligation",
                    severity="ERROR",
                    file_path=str(self.config.get_db_path()),
                    line=1,
                    rule_code=f"{rule_prefix}-MISSING",
                    message=(
                        f"{len(tagged)} document(s) declare '{llm_tag}' but no {run_label} "
                        f"verdict exists in the cache DB. Run {command} — a declared semantic "
                        "audit that never ran is not an audit."
                    ),
                )
            )
            return [], set()

        # No recorded hashes at all means a verdict whose subject cannot be
        # identified -- that is not evidence about the current specification.
        if not hashes:
            issues.append(
                VerificationIssue(
                    gate="Obligation",
                    severity="ERROR",
                    file_path=str(self.config.get_db_path()),
                    line=1,
                    rule_code=f"{rule_prefix}-UNANCHORED",
                    message=(
                        f"The {run_label} verdict records no document hashes, so there is no "
                        "way to tell which version of the specification it audited. A verdict "
                        "that cannot be tied to a document state cannot discharge an "
                        f"obligation — re-run {command} to produce an anchored verdict."
                    ),
                )
            )
            return entries, set()

        for doc in tagged:
            recorded = hashes.get(doc.file_path)
            if recorded is not None and recorded != doc.content_hash:
                issues.append(
                    VerificationIssue(
                        gate="Obligation",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=1,
                        rule_code=f"{rule_prefix}-STALE",
                        message=(
                            f"Document declares '{llm_tag}' but has changed since the "
                            f"{run_label} audited it. The stored verdict describes an earlier "
                            f"version of this text — re-run {command}."
                        ),
                    )
                )

        # Coverage used to be inferred by looking for the document's path anywhere
        # in the report text. That was wrong in both directions: a document that
        # passed cleanly contributed no issue text and read as never audited, while
        # a document merely named inside some other keyword's issue prose read as
        # audited. Verdicts record the files they were actually formed over.
        covered: set[str] = set()
        for e in entries:
            covered.update(e.get("covered_files", []) or [])

        for doc in tagged:
            if doc.file_path not in covered:
                missing_out.append(doc.file_path)
                issues.append(
                    VerificationIssue(
                        gate="Obligation",
                        severity="ERROR",
                        file_path=doc.file_path,
                        line=1,
                        rule_code=f"{rule_prefix}-SKIPPED",
                        message=(
                            f"Document declares '{llm_tag}' but does not appear in the "
                            f"{run_label} verdict. {skipped_hint}"
                        ),
                    )
                )

        return entries, covered
