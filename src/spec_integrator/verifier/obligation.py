from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument
from spec_integrator.verifier.static import VerificationIssue


TAG_BY_RECOMMENDATION = {
    "pymodelchecking": "{VERIFY_FORMAL}",
    "formal": "{VERIFY_FORMAL}",
    "llm_judge": "{VERIFY_LLM}",
    "llm": "{VERIFY_LLM}",
}


@dataclass
class ObligationSummary:
    assessed_documents: int = 0
    stale_documents: list[str] = field(default_factory=list)
    unassessed_documents: list[str] = field(default_factory=list)
    demanded: int = 0
    discharged: int = 0
    skipped: list[dict] = field(default_factory=list)
    judge_missing: list[str] = field(default_factory=list)
    sections_total: int = 0
    sections_assessed: int = 0

    @property
    def coverage(self) -> float:
        if self.demanded == 0:
            return 1.0
        return self.discharged / self.demanded


class ObligationVerifier:
    """Obligation Gate.

    The risk assessment (`assess`) decides *what must be verified*. Previously that
    verdict was written to a report and then ignored, so a section could be rated
    "risk 5/5, formal verification required" and still pass every gate. This gate
    closes the loop: an unmet verification obligation is an ERROR.
    """

    def __init__(self, config: Config):
        self.config = config

    def verify(self, documents: list[ParsedDocument]
               ) -> tuple[list[VerificationIssue], ObligationSummary]:
        summary = ObligationSummary()
        cfg = self.config.obligation
        if not cfg.enabled:
            return [], summary

        issues: list[VerificationIssue] = []
        risk_path = self.config.resolve_path(cfg.risk_report)

        payload = self._load_json(risk_path)
        if payload is None:
            if cfg.require_assessment:
                issues.append(VerificationIssue(
                    gate="Obligation", severity="ERROR",
                    file_path=cfg.risk_report, line=1,
                    rule_code="OBLIG-ASSESSMENT-MISSING",
                    message=("No risk assessment found. The pipeline cannot claim the specification "
                             "is verified without first deciding what needs verifying. "
                             "Run 'spec-integrator assess' before 'check'.")
                ))
            return issues, summary

        assessments = payload.get("assessments", []) or []
        doc_hashes = payload.get("doc_hashes", {}) or {}
        doc_map = {d.file_path: d for d in documents}

        # Coverage must be measured against the sections that exist now, not against
        # the ones the assessment happened to look at. "13/13 discharged" is not a
        # clean bill of health when the obligations were derived from 15 of 663
        # sections — the other 648 have unknown obligations, not zero.
        # A mock assessor infers "what must be verified" from the tags the document
        # already carries. The Obligation Gate then checks those same tags, so the
        # discharge rate is 1.0 by construction, whatever the specification says.
        backend = str(payload.get("backend", "") or "").lower()
        if not backend:
            issues.append(VerificationIssue(
                gate="Obligation", severity="ERROR",
                file_path=cfg.risk_report, line=1,
                rule_code="OBLIG-ASSESSMENT-PROVENANCE-UNKNOWN",
                message=("The risk assessment records no backend, so its independence from the "
                         "documents it judges cannot be established. Re-run 'assess' with a "
                         "tool version that stamps the engine.")
            ))
        elif backend in cfg.forbidden_backends:
            issues.append(VerificationIssue(
                gate="Obligation", severity="ERROR",
                file_path=cfg.risk_report, line=1,
                rule_code="OBLIG-ASSESSMENT-NOT-INDEPENDENT",
                message=(f"The risk assessment was produced by the '{backend}' backend, which "
                         "derives each obligation from the tags the document already carries. "
                         "The discharge rate is then true by construction and says nothing about "
                         "the specification. Re-run 'assess' against a real backend.")
            ))

        summary.sections_total = sum(len(d.sections) for d in documents)
        summary.sections_assessed = int(payload.get("total_evaluated", 0) or 0)
        if cfg.require_full_coverage and summary.sections_assessed < summary.sections_total:
            issues.append(VerificationIssue(
                gate="Obligation", severity="ERROR",
                file_path=cfg.risk_report, line=1,
                rule_code="OBLIG-ASSESSMENT-PARTIAL",
                message=(f"Only {summary.sections_assessed} of {summary.sections_total} section(s) "
                         "were risk-assessed, so the verification obligations of the remainder are "
                         "unknown. A discharge rate computed over a partial assessment does not "
                         "mean the specification is covered. Re-run 'assess --exhaustive'.")
            ))

        # --- 1. Staleness: the assessment must describe the documents as they are now ---
        assessed_files = {a.get("file_path") for a in assessments if a.get("file_path")}
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
                    issues.append(VerificationIssue(
                        gate="Obligation", severity="ERROR",
                        file_path=doc.file_path, line=1,
                        rule_code="OBLIG-ASSESSMENT-STALE",
                        message=("Document changed since it was risk-assessed. The recorded "
                                 "verification obligations no longer describe this content — "
                                 "re-run 'spec-integrator assess'.")
                    ))

        # --- 2. Demanded verification must actually be tagged and performed ---
        for a in assessments:
            file_path = a.get("file_path")
            heading = a.get("heading", "")
            risk = int(a.get("risk_score", 0) or 0)
            formal_needed = bool(a.get("formal_needed", False))
            recommended = str(a.get("recommended_verification", "") or "").lower()

            demanded_tags = {t for t in (a.get("suggested_tags") or []) if t.startswith("{VERIFY_")}
            mapped = TAG_BY_RECOMMENDATION.get(recommended)
            if mapped:
                demanded_tags.add(mapped)

            # A recommendation IS a demand, independent of the numeric risk
            # score: `_call_heuristic` hardcodes risk=3 for its LLM_Judge
            # branch (vs. risk=4 for formal), so gating solely on
            # `risk >= cfg.risk_threshold` (4) meant every LLM_Judge
            # recommendation was silently never "demanded" -- the check
            # below never ran for a single one of them.
            is_demanded = formal_needed or recommended in ("llm_judge", "llm") or risk >= cfg.risk_threshold
            if not is_demanded or not demanded_tags:
                continue

            doc = doc_map.get(file_path)
            if doc is None:
                continue

            summary.demanded += 1
            present = set(doc.all_tags)
            missing = sorted(t for t in demanded_tags if t not in present)

            if not missing:
                summary.discharged += 1
                continue

            summary.skipped.append({
                "file_path": file_path, "heading": heading,
                "risk_score": risk, "missing_tags": missing,
            })
            issues.append(VerificationIssue(
                gate="Obligation", severity="ERROR",
                file_path=file_path, line=self._line_of(doc, heading),
                rule_code="OBLIG-VERIFICATION-SKIPPED",
                message=(f"Section '{heading}' was assessed at risk {risk}/5 and requires "
                         f"{', '.join(missing)}, but the document carries no such tag, so the "
                         "verification is never executed. Add the tag and supply the evidence, "
                         "or record an explicit, justified waiver.")
            ))

        # --- 3. {VERIFY_LLM} must have actually been judged ---
        if cfg.require_judge:
            issues.extend(self._verify_judge_coverage(documents, summary))

        return issues, summary

    # ------------------------------------------------------------------ #
    def _verify_judge_coverage(self, documents: list[ParsedDocument],
                               summary: ObligationSummary) -> list[VerificationIssue]:
        cfg = self.config.obligation
        llm_tag = self.config.llm_judge.tag
        tagged = [d for d in documents if llm_tag in d.all_tags]
        if not tagged:
            return []

        judge_path = self.config.resolve_path(cfg.judge_report)
        payload = self._load_json(judge_path)
        if payload is None:
            summary.judge_missing = [d.file_path for d in tagged]
            return [VerificationIssue(
                gate="Obligation", severity="ERROR",
                file_path=cfg.judge_report, line=1,
                rule_code="OBLIG-JUDGE-MISSING",
                message=(f"{len(tagged)} document(s) declare '{llm_tag}' but no LLM judge report "
                         "exists. Run 'spec-integrator judge' — a declared semantic audit that never "
                         "ran is not an audit.")
            )]

        entries = payload if isinstance(payload, list) else payload.get("results", [])

        issues: list[VerificationIssue] = []

        # --- Staleness: the verdict must describe the documents as they are now ---
        # A judge report carries no hashes if it was produced before this check
        # existed; treat that as stale too, because a verdict whose subject cannot
        # be identified is not evidence about the current specification.
        judge_hashes = payload.get("doc_hashes", {}) if isinstance(payload, dict) else {}
        if not judge_hashes:
            return issues + [VerificationIssue(
                gate="Obligation", severity="ERROR",
                file_path=cfg.judge_report, line=1,
                rule_code="OBLIG-JUDGE-UNANCHORED",
                message=("The LLM judge report records no document hashes, so there is no way to "
                         "tell which version of the specification it audited. A verdict that "
                         "cannot be tied to a document state cannot discharge an obligation — "
                         "re-run 'spec-integrator judge' to produce an anchored report.")
            )]

        for doc in tagged:
            recorded = judge_hashes.get(doc.file_path)
            if recorded is not None and recorded != doc.content_hash:
                issues.append(VerificationIssue(
                    gate="Obligation", severity="ERROR",
                    file_path=doc.file_path, line=1,
                    rule_code="OBLIG-JUDGE-STALE",
                    message=(f"Document declares '{llm_tag}' but has changed since the LLM judge "
                             "audited it. The stored verdict describes an earlier version of this "
                             "text — re-run 'spec-integrator judge'.")
                ))

        # Real `judge` output is keyword-centric: {"item_label": "{Keyword}",
        # "status": ...}, not the {"subgraph"|"item"|"target": ...} shape this
        # used to look for. That mismatch meant `failed` was always empty in
        # practice, so a FAIL verdict from a real judge run could never
        # surface here -- this is the first time this path has run against
        # real (rather than hand-shaped test) judge output.
        failed_keywords = {
            e.get("item_label", "").strip("{}")
            for e in entries if isinstance(e, dict) and e.get("status") == "FAIL"
        }

        # Coverage used to be inferred by looking for the document's path anywhere
        # in the report text. That was wrong in both directions: a document that
        # passed cleanly contributed no issue text and read as never audited, while
        # a document merely named inside some other keyword's issue prose read as
        # audited. Verdicts now record the files they were actually formed over.
        covered: set[str] = set()
        for e in entries:
            if isinstance(e, dict):
                covered.update(e.get("covered_files", []) or [])

        for doc in tagged:
            if doc.file_path not in covered:
                summary.judge_missing.append(doc.file_path)
                issues.append(VerificationIssue(
                    gate="Obligation", severity="ERROR",
                    file_path=doc.file_path, line=1,
                    rule_code="OBLIG-JUDGE-SKIPPED",
                    message=(f"Document declares '{llm_tag}' but does not appear in the judge report. "
                             "Raise --max-subgraphs so the audit actually covers it.")
                ))
                continue
            for kw in sorted(failed_keywords & set(doc.all_keywords)):
                issues.append(VerificationIssue(
                    gate="Obligation", severity="ERROR",
                    file_path=doc.file_path, line=1,
                    rule_code="OBLIG-JUDGE-FAILED",
                    message=(f"LLM semantic audit reported FAIL for '{{{kw}}}', which this document "
                             "cites, in the stored judge report.")
                ))
        return issues

    @staticmethod
    def _line_of(doc: ParsedDocument, heading: str) -> int:
        for sec in doc.sections:
            if sec.heading == heading:
                return sec.line_start
        return 1

    @staticmethod
    def _load_json(path: Path):
        try:
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
