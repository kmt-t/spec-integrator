from __future__ import annotations

import time

from spec_integrator.config import Config
from spec_integrator.judge.base import BaseJudge
from spec_integrator.models import JudgeReport, JudgeResult, ParsedDocument

JUDGE_PROMPT_TEMPLATE = """You are a strict, formal System Specification Verification Judge.
Your mission is to perform an exhaustive, evidence-based consistency and contradiction audit between a Requirement/Definition section and its referencing Design sections.

Target Keyword/Requirement ID: {item_label}

=== DEFINITION SECTIONS ===
{definition_texts}

=== REFERENCING DESIGN SECTIONS ===
{referencing_texts}

=== EVALUATION CRITERIA ===
Perform your audit systematically against the following 7 core criteria:

1. Vertical Consistency (Definition vs. Designs):
   Audit whether referencing design sections strictly conform to the DEFINITION.
   - Detect conflicting assumptions, mismatched parameters, invalidated preconditions, or broken invariants.

2. Horizontal Consistency (Cross-Design Pairwise Audit):
   Cross-compare all referencing design sections against each other pairwise.
   - Even if both conform to an abstract definition, detect cross-document drift: mismatched register assignments, differing buffer/bank counts, conflicting state names, incompatible call signatures, contradictory ordering guarantees, or one section claiming an operation is zero-cost while another details runtime overhead.

3. Numeric Agreement & Arithmetic:
   Verify mathematical and numeric integrity across all sections.
   - Re-calculate totals, allocations, budgets, and offsets. Verify that sub-allocations sum exactly to stated totals and that identically named constants/quantities have identical values everywhere. Report any arithmetic discrepancy as an ERROR with the disagreeing sections cited.

4. Completeness & Constraint Fulfillment:
   Audit whether referencing designs fulfill all mandatory rules, constraints, error-handling policies, and lifecycle requirements specified in the definition.

5. Clarity & Unresolved Ambiguity:
   Detect underspecified protocols, missing fallback behaviours, unassigned states, or ambiguous boundary conditions left unresolved.

6. Redundancy & Duplication Audit (Avoid Stale Duplication, Permit Layered Perspectives):
   Audit the texts for copy-pasted, redundant, or multi-location duplication of concrete specifications.
   - PERMITTED AND ENCOURAGED (Legitimate Layered / Multi-perspective Descriptions):
     Describing the same design subject from DIFFERENT architectural abstraction layers or complementary viewpoints
     is valid and must NOT be flagged as duplication. Examples of legitimate layering:
       * Tier 1 (Architecture): High-level system goals, 6 pillars summary, end-to-end rationale.
       * Tier 2 (Subsystem/Manager): Component lifecycle, state machines, API integration, fallback policies.
       * Tier 3 (Leaf Component): Exact byte/bitfield layouts, physical offsets, register assignments, stencils.
       * Formal/Verifier: Mathematical properties, invariants, proof assertions, empirical benchmarks.
   - FLAGGED AS REDUNDANT (WARNING / ERROR):
     * If two or more sections duplicate the SAME level of detailed technical specification (e.g., verbatim
       copy-pasted struct tables, duplicated binary layouts, identical step-by-step algorithm lists) instead
       of establishing a single Source of Truth and referencing it via link, flag as a WARNING (Redundant Specification Duplication).
     * If duplicated descriptions have drifted, mismatched parameters, or conflicting details between copies,
       flag as an ERROR (Drifted Duplicate / Inconsistency).

7. Claim-Evidence Substantiation & Unbacked Assertions:
   Identify all factual, safety, or empirical claims made in the prose (e.g., "formally verified",
   "proven deadlock-free", "zero overhead", "measured on Cortex-M7", specific benchmarks or cycles).
   - If a section claims a property is "proven", "verified", or "measured", check whether the text
     explicitly cites a concrete artifact (formal model file, concept test, WIT interface, or benchmark log).
   - An assertion of a verification without citing the backing artifact, or relying on a vacuous/unrelated
     model, is an ERROR (Unbacked Verification Claim).
   - A bare empirical measurement stated as an accomplished fact without an environment or artifact
     reference is a WARNING (Unsourced Metric / Measurement).
   - Valid design estimates, aspirations, or budgeted targets ("目標", "想定", "target", "budgeted") are
     acceptable and must not be flagged as errors.

=== AUDITOR RULES ===
- Literal Evaluation: Judge what the text actually and explicitly states, not what it might have intended.
- No Vacuous Confirmation: Restating a section's claim back as confirmation is not an audit.
- Specific Citations: When reporting contradictions, duplicates, or missing citations, always cite the specific file and section names on both sides.
- No False Positives: If the sections agree and meet all criteria, state so concisely as PASS; do not manufacture non-existent issues.

=== OUTPUT FORMAT ===
Respond ONLY with a valid JSON object in English in the following format:
```json
{{
  "status": "PASS" | "WARN" | "FAIL",
  "summary": "Concise explanation of the evaluation result in English",
  "issues": [
    {{
      "severity": "ERROR" | "WARNING",
      "location": "File or Section name",
      "description": "Detailed explanation of contradiction, duplicate, unbacked claim, or missing spec in English"
    }}
  ]
}}
```
"""


DOCUMENT_JUDGE_PROMPT_TEMPLATE = """You are a strict, formal System Specification Verification Judge.
Your mission is to perform an exhaustive, evidence-based self-consistency audit of a single
specification document, taken as a whole -- independent of how its individual keywords are
cross-checked against other documents elsewhere.

File: {file_path} (Tier: {tier})

=== DOCUMENT CONTENT ===
{content}

=== EVALUATION CRITERIA ===
Perform your audit systematically against the following 4 core criteria:

1. Internal Consistency:
   Detect contradictions within this document itself -- mismatched parameters, conflicting
   state names, incompatible values for the same quantity, or claims that undermine each other
   in different sections of the same file.

2. Numeric Agreement & Arithmetic:
   Re-calculate totals, allocations, budgets, and offsets stated within this document. Verify
   sub-allocations sum exactly to stated totals and that identically named constants/quantities
   have identical values everywhere in the file. Report any arithmetic discrepancy as an ERROR.

3. Clarity & Unresolved Ambiguity:
   Detect underspecified protocols, missing fallback behaviours, unassigned states, or ambiguous
   boundary conditions left unresolved anywhere in the document.

4. Claim-Evidence Substantiation & Unbacked Assertions:
   Identify all factual, safety, or empirical claims made in the prose (e.g., "formally verified",
   "proven deadlock-free", "zero overhead", "measured on Cortex-M7", specific benchmarks or cycles).
   - If the document claims a property is "proven", "verified", or "measured", check whether the
     text explicitly cites a concrete artifact (formal model file, concept test, WIT interface, or
     benchmark log).
   - An assertion of a verification without citing the backing artifact, or relying on a
     vacuous/unrelated model, is an ERROR (Unbacked Verification Claim).
   - A bare empirical measurement stated as an accomplished fact without an environment or artifact
     reference is a WARNING (Unsourced Metric / Measurement).
   - Valid design estimates, aspirations, or budgeted targets ("目標", "想定", "target", "budgeted")
     are acceptable and must not be flagged as errors.

=== AUDITOR RULES ===
- Literal Evaluation: Judge what the text actually and explicitly states, not what it might have intended.
- No Vacuous Confirmation: Restating the document's claim back as confirmation is not an audit.
- Specific Citations: When reporting contradictions or missing citations, cite the specific section
  heading(s) within this document.
- No False Positives: If the document is internally consistent and meets all criteria, state so
  concisely as PASS; do not manufacture non-existent issues.
- Cross-document consistency is out of scope here -- that is audited separately per requirement
  keyword. Do not flag something as inconsistent with another file you were not shown.

=== OUTPUT FORMAT ===
Respond ONLY with a valid JSON object in English in the following format:
```json
{{
  "status": "PASS" | "WARN" | "FAIL",
  "summary": "Concise explanation of the evaluation result in English",
  "issues": [
    {{
      "severity": "ERROR" | "WARNING",
      "location": "Section name within this document",
      "description": "Detailed explanation of contradiction, unbacked claim, or ambiguity in English"
    }}
  ]
}}
```
"""


class SemanticJudge(BaseJudge):
    """Evaluates semantic consistency, completeness, and contradictions

    between specification definitions and their referencing design sections
    using an LLM as a Judge.
    """

    def __init__(self, config: Config):
        super().__init__(config)

    def judge_subgraphs(
        self,
        subgraphs: list[dict],
        documents: list[ParsedDocument],
        backend: str | None = None,
        model: str | None = None,
        max_subgraphs: int = 10,
        exhaustive: bool = False,
        min_references: int = 1,
        changed_sections: set[str] | None = None,
    ) -> JudgeReport:
        report = JudgeReport()
        selected_backend = backend or self.config.llm_judge.default_backend
        llm_tag = self.config.llm_judge.tag

        if exhaustive:
            target_subgraphs = [
                sg for sg in subgraphs if len(sg.get("referenced_in", [])) >= min_references
            ]
        else:
            tagged_subgraphs = []
            for sg in subgraphs:
                has_tag = False
                for sec_id in sg["defined_in"] + sg["referenced_in"]:
                    doc, sec = self._find_doc_and_sec(sec_id, documents)
                    if doc and (llm_tag in doc.all_tags or (sec and llm_tag in sec.tags)):
                        has_tag = True
                        break
                if has_tag:
                    tagged_subgraphs.append(sg)

            target_subgraphs = (
                tagged_subgraphs
                if tagged_subgraphs
                else [sg for sg in subgraphs if len(sg.get("referenced_in", [])) >= min_references]
            )

        if changed_sections is not None:
            target_subgraphs = [
                sg
                for sg in target_subgraphs
                if (set(sg.get("defined_in", [])) | set(sg.get("referenced_in", [])))
                & changed_sections
            ]

        target_candidates = (
            target_subgraphs[:max_subgraphs] if max_subgraphs > 0 else target_subgraphs
        )

        print(
            f"Auditing {len(target_candidates)} requirement subgraph(s) using LLM Backend: '{selected_backend}'..."
        )
        if exhaustive:
            print(
                f"  (Exhaustive mode: checking all subgraphs with >= {min_references} reference(s))"
            )

        for idx, sg in enumerate(target_candidates, start=1):
            ref_count = len(sg.get("referenced_in", []))
            print(
                f"  [{idx}/{len(target_candidates)}] Evaluating '{sg['item_label']}' ({ref_count} reference(s))...",
                flush=True,
            )
            res = self._evaluate_single_subgraph(sg, documents, selected_backend, model)
            report.results.append(res)
            if res.status == "PASS":
                report.pass_count += 1
            elif res.status == "WARN":
                report.warn_count += 1
            elif res.status == "FAIL":
                report.fail_count += 1

            badge = "PASS" if res.status == "PASS" else ("WARN" if res.status == "WARN" else "FAIL")
            print(f"       -> Result: {badge} ({res.summary[:80]})", flush=True)

        report.total_evaluated = len(report.results)
        return report

    def judge_documents(
        self,
        documents: list[ParsedDocument],
        backend: str | None = None,
        model: str | None = None,
        max_documents: int = 15,
        exhaustive: bool = False,
        include_meta: bool = False,
        include_reqs: bool = False,
        target_tiers: list[int | str] | None = None,
    ) -> JudgeReport:
        report = JudgeReport()
        selected_backend = backend or self.config.llm_judge.default_backend
        llm_tag = self.config.llm_judge.tag

        if exhaustive:
            target_candidates = documents if max_documents <= 0 else documents[:max_documents]
        else:
            tagged_docs = [d for d in documents if llm_tag in d.all_tags]
            cands = tagged_docs if tagged_docs else documents
            target_candidates = cands if max_documents <= 0 else cands[:max_documents]

        print(
            f"Auditing {len(target_candidates)} whole document(s) using LLM Backend: '{selected_backend}'..."
        )
        if exhaustive:
            print("  (Exhaustive mode: evaluating all documents across all tiers)")

        for idx, doc in enumerate(target_candidates, start=1):
            print(f"  [{idx}/{len(target_candidates)}] Evaluating '{doc.file_path}'...", flush=True)
            res = self._evaluate_single_document(doc, selected_backend, model)
            report.results.append(res)
            if res.status == "PASS":
                report.pass_count += 1
            elif res.status == "WARN":
                report.warn_count += 1
            elif res.status == "FAIL":
                report.fail_count += 1

            badge = "PASS" if res.status == "PASS" else ("WARN" if res.status == "WARN" else "FAIL")
            print(f"       -> Result: {badge} ({res.summary[:80]})", flush=True)

        report.total_evaluated = len(report.results)
        return report

    def _evaluate_single_document(
        self, doc: ParsedDocument, backend: str, model: str | None
    ) -> JudgeResult:
        prompt = DOCUMENT_JUDGE_PROMPT_TEMPLATE.format(
            file_path=doc.file_path,
            tier=doc.tier,
            content=self._budgeted(doc.content),
        )
        return self._run_judge_llm(
            prompt, doc.file_path, doc.file_path, [doc.file_path], backend, model
        )

    def _evaluate_single_subgraph(
        self, sg: dict, documents: list[ParsedDocument], backend: str, model: str | None
    ) -> JudgeResult:
        item_label = sg["item_label"]
        covered = self._covered_files(sg)
        def_texts = [
            f"--- Section: {sid} ---\n{self._retrieve_section_content(sid, documents)}"
            for sid in sg["defined_in"]
        ]
        ref_texts = [
            f"--- Section: {sid} ---\n{self._retrieve_section_content(sid, documents)}"
            for sid in sg["referenced_in"]
        ]

        prompt = JUDGE_PROMPT_TEMPLATE.format(
            item_label=item_label,
            definition_texts="\n\n".join(def_texts)
            if def_texts
            else "(No explicit definition section)",
            referencing_texts="\n\n".join(ref_texts) if ref_texts else "(No referencing sections)",
        )
        return self._run_judge_llm(prompt, sg["item_id"], item_label, covered, backend, model)

    def _run_judge_llm(
        self,
        prompt: str,
        item_id: str,
        item_label: str,
        covered: list[str],
        backend: str,
        model: str | None,
    ) -> JudgeResult:
        if backend == "mock":
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="PASS",
                summary="Mock evaluation passed.",
                issues=[],
                covered_files=covered,
            )
        if backend not in ("sakura", "ollama", "openrouter"):
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="SKIPPED",
                summary=f"Unknown backend '{backend}'.",
                issues=[],
                covered_files=covered,
            )

        last_err: Exception | None = None
        parsed: dict | None = None
        for attempt in range(3):
            try:
                if backend == "sakura":
                    raw_resp = self._call_sakura(prompt, model)
                elif backend == "openrouter":
                    raw_resp = self._call_openrouter(prompt, model)
                else:
                    raw_resp = self._call_ollama(prompt, model)
                candidate = self._extract_json(raw_resp)
                if not candidate.get("status"):
                    raise ValueError("response JSON has no 'status' field")
                parsed = candidate
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(2)

        if parsed is None:
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="FAIL",
                summary=f"Judge error after 3 attempts: {last_err}",
                issues=[
                    {
                        "severity": "ERROR",
                        "location": item_label,
                        "description": f"No usable verdict after 3 attempts: {last_err}",
                    }
                ],
                covered_files=covered,
            )

        issues = parsed.get("issues", []) or []
        status = parsed["status"]
        if status == "PASS" and any(
            str(i.get("severity", "")).upper() == "ERROR" for i in issues if isinstance(i, dict)
        ):
            status = "FAIL"

        return JudgeResult(
            item_id=item_id,
            item_label=item_label,
            status=status,
            summary=parsed.get("summary", ""),
            issues=issues,
            covered_files=covered,
        )


__all__ = ["JudgeReport", "JudgeResult", "SemanticJudge"]
