from __future__ import annotations

import json
from dataclasses import dataclass

from spec_integrator.config import LLMCheckRule
from spec_integrator.graph import KeywordGroup
from spec_integrator.judge.base import BaseJudge
from spec_integrator.judge.checksheet import Checksheet
from spec_integrator.judge.llm_backend import (
    BACKEND_LABELS,
    SYSTEM_ONE_BACKENDS,
    LLMBackendError,
)
from spec_integrator.models import JudgeEvaluation, JudgeResult, ParsedDocument, ParsedSection


@dataclass(frozen=True)
class _KeywordLinkPair:
    keyword: str
    item_id: str
    item_label: str
    definition: tuple[ParsedDocument, ParsedSection] | None
    reference: tuple[ParsedDocument, ParsedSection]


REVIEW_OUTCOMES = {
    "confirmed_violation": "A specific, unmistakable defect is directly visible and this criterion applies.",
    "possible_violation": "Specific evidence suggests a defect, but a human must confirm it.",
    "documented_open_issue": "The text explicitly leaves this matter unresolved or out of scope.",
    "insufficient_context": "The supplied sections do not provide enough information to judge this criterion.",
    "improvement_suggestion": "The criterion is met, but a non-blocking improvement is possible.",
    "no_issue": "The supplied evidence supports no issue for this criterion.",
}
CLEF_FLASH_DEFAULT_CONTEXT_TOKENS = 65536
CLEF_FLASH_CONTEXT_BUDGET_BASELINE_TOKENS = 8192
CLEF_FLASH_REVIEW_CONTEXT_CHAR_BUDGET = 3000
CLEF_FLASH_CHECKS_PER_REQUEST = 2


class UnifiedReviewEngine(BaseJudge):
    """Unified engine for all LLM document audits:

    - Single document internal review
    - Definition/reference keyword link-pair review
    - Modular, configurable check rules defined strictly in configuration
    """

    def get_effective_checks(
        self,
        mode: str,
        check_ids: list[str] | None = None,
        include_disabled: bool = False,
    ) -> list[LLMCheckRule]:
        """Collects effective check rules defined in configuration."""
        rules = self.config.llm_judge.checks

        selected: list[LLMCheckRule] = []
        for r in rules:
            if not include_disabled and not r.enabled:
                continue
            if mode not in r.mode:
                continue
            if check_ids and r.id not in check_ids:
                continue
            selected.append(r)
        return selected

    def build_checksheet(
        self,
        mode: str,
        target_name: str,
        sections_context: str,
        checks: list[LLMCheckRule],
        covered_files: list[str],
    ) -> Checksheet:
        """Build the same typed checksheet used for dry runs and live reviews."""
        questions: dict[str, dict[str, object]] = {}
        for check in checks:
            questions[check.id] = {
                "type": "choice",
                "instructions": (
                    "Using only `specification_content`, classify the evidence for this review "
                    f"criterion: {check.name}. Select one outcome. Do not infer requirements "
                    "or defects from absent context. Prefer `possible_violation` or "
                    "`insufficient_context` when evidence is ambiguous. "
                    f"Criterion instructions: {check.get_prompt_text(self.config.config_dir)}"
                ),
                "criteria": REVIEW_OUTCOMES,
            }
        return Checksheet(
            name="document_review",
            state={
                "review_target": target_name,
                "review_mode": mode,
                "covered_files": covered_files,
                "specification_content": sections_context,
            },
            questions=questions,
        )

    def review_single_document(
        self,
        doc: ParsedDocument,
        backend: str | None = None,
        model: str | None = None,
        check_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> JudgeResult:
        """Reviews each section independently for internal consistency and standards."""
        checks = self.get_effective_checks("single", check_ids=check_ids)
        if not checks:
            return JudgeResult(
                item_id=doc.file_path,
                item_label=doc.file_path,
                status="SKIPPED",
                summary="No active checks configured for single document review.",
                covered_files=[doc.file_path],
            )

        if not doc.sections:
            return JudgeResult(
                item_id=doc.file_path,
                item_label=doc.file_path,
                status="SKIPPED",
                summary="No sections were available for section-level review.",
                covered_files=[doc.file_path],
            )

        selected_backend = backend or self.config.llm_judge.default_backend
        section_char_budget = self._section_content_budget(selected_backend, 1)
        section_results: list[tuple[str, JudgeResult]] = []
        for sec in doc.sections:
            section_ref = f"{doc.file_path}#{sec.heading}"
            context_lines = [
                f"File: {doc.file_path} (Tier: {doc.tier})",
                (
                    f"Section: {sec.heading} "
                    f"(ID: {sec.section_id}; Lines {sec.line_start}-{sec.line_end})"
                ),
            ]
            if sec.keywords:
                context_lines.append(f"Section Keywords: {', '.join(sec.keywords)}")
            context_lines.extend(["", self._budgeted(sec.body_text, section_char_budget)])
            context_text = "\n".join(context_lines)
            sheet = self.build_checksheet(
                "single", section_ref, context_text, checks, [doc.file_path]
            )

            if dry_run:
                print(f"=== DRY-RUN CHECKSHEET FOR SECTION: {section_ref} ===")
                print(json.dumps(sheet.payload(), ensure_ascii=False, indent=2))
                print("=" * 80)
                result = JudgeResult(
                    item_id=f"{doc.file_path}::{sec.section_id}",
                    item_label=section_ref,
                    status="PASS",
                    summary="[Dry Run] Section checksheet generated successfully.",
                    covered_files=[doc.file_path],
                )
            else:
                result = self._run_checksheet_batches(
                    sheet,
                    f"{doc.file_path}::{sec.section_id}",
                    section_ref,
                    [doc.file_path],
                    selected_backend,
                    model,
                    checks=checks,
                )
                for issue in result.issues:
                    if not issue.get("location"):
                        issue["location"] = section_ref
            section_results.append((section_ref, result))

        statuses = [result.status for _, result in section_results]
        if "FAIL" in statuses:
            status = "FAIL"
        elif "WARN" in statuses:
            status = "WARN"
        elif all(section_status == "SKIPPED" for section_status in statuses):
            status = "SKIPPED"
        else:
            status = "PASS"

        counts = {value: statuses.count(value) for value in ("PASS", "WARN", "FAIL", "SKIPPED")}
        summary = (
            f"Reviewed {len(section_results)} sections independently: "
            f"{counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL, "
            f"{counts['SKIPPED']} SKIPPED."
        )
        if dry_run:
            summary = f"[Dry Run] Generated {len(section_results)} section checksheets."

        issues = [issue for _, result in section_results for issue in result.issues]
        evaluations = [
            evaluation for _, result in section_results for evaluation in result.evaluations
        ]
        return JudgeResult(
            item_id=doc.file_path,
            item_label=doc.file_path,
            status=status,
            summary=summary,
            issues=issues,
            evaluations=evaluations,
            covered_files=[doc.file_path],
        )

    def review_keyword_link_pairs(
        self,
        group: KeywordGroup,
        documents: list[ParsedDocument],
        backend: str | None = None,
        model: str | None = None,
        check_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> list[JudgeResult]:
        """Reviews each canonical-definition/reference section pair separately."""
        checks = self.get_effective_checks("link_pair", check_ids=check_ids)
        pairs = self._collect_keyword_link_pairs(group, documents)
        if not pairs:
            return [
                JudgeResult(
                    item_id=group.group_id,
                    item_label=group.keyword,
                    status="SKIPPED",
                    summary="No definition/reference link pairs were available for review.",
                    covered_files=[],
                )
            ]
        if not checks:
            return [
                JudgeResult(
                    item_id=pair.item_id,
                    item_label=pair.item_label,
                    status="SKIPPED",
                    summary="No active checks configured for keyword link-pair review.",
                    covered_files=self._pair_covered_files(pair),
                )
                for pair in pairs
            ]

        selected_backend = backend or self.config.llm_judge.default_backend
        section_char_budget = self._section_content_budget(selected_backend, 2)
        results: list[JudgeResult] = []
        print(f"Reviewing {len(pairs)} definition/reference link pair(s)...", flush=True)
        for index, pair in enumerate(pairs, start=1):
            print(f"  [{index}/{len(pairs)}] {pair.item_label}", flush=True)
            context_text = self._keyword_link_pair_context(pair, section_char_budget)
            sheet = self.build_checksheet(
                "link_pair", pair.item_label, context_text, checks, self._pair_covered_files(pair)
            )
            if dry_run:
                print(
                    f"=== DRY-RUN LINK-PAIR CHECKSHEET [{index}/{len(pairs)}]: "
                    f"{pair.item_label} ({pair.item_id}) ==="
                )
                print(json.dumps(sheet.payload(), ensure_ascii=False, indent=2))
                print("=" * 80)
                result = JudgeResult(
                    item_id=pair.item_id,
                    item_label=pair.item_label,
                    status="PASS",
                    summary="[Dry Run] Definition/reference link-pair checksheet generated.",
                    covered_files=self._pair_covered_files(pair),
                )
            else:
                result = self._run_checksheet_batches(
                    sheet,
                    pair.item_id,
                    pair.item_label,
                    self._pair_covered_files(pair),
                    selected_backend,
                    model,
                    checks=checks,
                )
                reference_doc, reference_section = pair.reference
                default_location = f"{reference_doc.file_path} :: {reference_section.heading}"
                for issue in result.issues:
                    if not issue.get("location"):
                        issue["location"] = default_location
            results.append(result)
        return results

    def _collect_keyword_link_pairs(
        self, group: KeywordGroup, documents: list[ParsedDocument]
    ) -> list[_KeywordLinkPair]:
        linked_section_ids = set(group.section_ids)
        doc_map = {document.file_path: document for document in documents}
        linked_sections = [
            (document, section)
            for file_path in group.file_paths
            if (document := doc_map.get(file_path)) is not None
            for section in document.sections
            if section.section_id in linked_section_ids
        ]
        definitions = sorted(
            [
                (document, section)
                for document in documents
                for section in document.sections
                # The declaration travels with its source section; the LLM sees only its text.
                if self.config.is_keyword_definition(
                    group.keyword, document.file_path, section.canonical_definition_keywords
                )
            ],
            key=lambda pair: (pair[0].file_path, pair[1].line_start, pair[1].section_id),
        )
        definition_section_ids = {section.section_id for _document, section in definitions}
        references = sorted(
            [
                (document, section)
                for document, section in linked_sections
                if section.section_id not in definition_section_ids
                and group.keyword in section.reference_keywords
            ],
            key=lambda pair: (pair[0].file_path, pair[1].line_start, pair[1].section_id),
        )
        definition_candidates: list[tuple[ParsedDocument, ParsedSection] | None] = definitions or [
            None
        ]

        pairs: list[_KeywordLinkPair] = []
        for definition in definition_candidates:
            for reference in references:
                definition_label = (
                    f"{definition[0].file_path} :: {definition[1].heading}"
                    if definition
                    else "(definition section missing)"
                )
                reference_label = f"{reference[0].file_path} :: {reference[1].heading}"
                definition_id = definition[1].section_id if definition else "missing-definition"
                item_id = f"linkpair:{group.keyword}:{definition_id}->{reference[1].section_id}"
                item_label = (
                    f"{{{group.keyword}}} | DEFINITION {definition_label} "
                    f"↔ REFERENCE {reference_label}"
                )
                pairs.append(
                    _KeywordLinkPair(
                        keyword=group.keyword,
                        item_id=item_id,
                        item_label=item_label,
                        definition=definition,
                        reference=reference,
                    )
                )
        return pairs

    def _keyword_link_pair_context(
        self, pair: _KeywordLinkPair, section_char_budget: int | None = None
    ) -> str:
        lines = [
            f"Keyword: {{{pair.keyword}}}",
            "Evaluation unit: one definition section paired with one reference section.",
            "Definitions are identified by a section-level definition declaration and substantive text in that section.",
            "References are identified by the section-level traceability comment.",
            "",
        ]

        def append_section(
            role: str, section_pair: tuple[ParsedDocument, ParsedSection] | None
        ) -> None:
            lines.append(f"### {role} SECTION")
            if section_pair is None:
                lines.extend(["(no definition declaration was found)", ""])
                return
            document, section = section_pair
            keywords = f" [Keywords: {', '.join(section.keywords)}]" if section.keywords else ""
            lines.extend(
                [
                    f"File: {document.file_path} (Tier: {document.tier})",
                    f"Section: {section.heading} (ID: {section.section_id}; "
                    f"lines {section.line_start}-{section.line_end}){keywords}",
                    self._budgeted(section.body_text, section_char_budget),
                    "",
                ]
            )

        append_section("DEFINITION", pair.definition)
        append_section("REFERENCE", pair.reference)
        return "\n".join(lines)

    def _section_content_budget(self, backend: str, section_count: int) -> int | None:
        """Bound Clef Flash evidence to its configured context window."""
        if backend != "clef-flash":
            return None
        assert section_count > 0
        backend_config = self.config.llm_judge.backends.get("clef-flash")
        context_window_tokens = (
            backend_config.context_window_tokens
            if backend_config and backend_config.context_window_tokens is not None
            else CLEF_FLASH_DEFAULT_CONTEXT_TOKENS
        )
        assert context_window_tokens > 0
        total_budget = (
            CLEF_FLASH_REVIEW_CONTEXT_CHAR_BUDGET
            * context_window_tokens
            // CLEF_FLASH_CONTEXT_BUDGET_BASELINE_TOKENS
        )
        configured_budget = self.config.llm_judge.section_char_budget
        if configured_budget > 0:
            total_budget = min(total_budget, configured_budget)
        return max(1, total_budget // section_count)

    @staticmethod
    def _pair_covered_files(pair: _KeywordLinkPair) -> list[str]:
        files = [pair.reference[0].file_path]
        if pair.definition:
            files.insert(0, pair.definition[0].file_path)
        return list(dict.fromkeys(files))

    def _run_checksheet(
        self,
        sheet: Checksheet,
        item_id: str,
        item_label: str,
        covered: list[str],
        backend: str,
        model: str | None,
        checks: list[LLMCheckRule],
    ) -> JudgeResult:
        """Classify one evidence unit with its checksheet."""
        if backend == "mock":
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="PASS",
                summary=f"Mock evaluation passed for '{item_label}'.",
                issues=[],
                covered_files=covered,
            )
        if backend not in SYSTEM_ONE_BACKENDS:
            raise ValueError(f"Unsupported checksheet backend: '{backend}'")
        return self._run_system_one_review(
            sheet, item_id, item_label, covered, backend, checks, model
        )

    def _run_checksheet_batches(
        self,
        sheet: Checksheet,
        item_id: str,
        item_label: str,
        covered: list[str],
        backend: str,
        model: str | None,
        checks: list[LLMCheckRule],
    ) -> JudgeResult:
        """Split Clef Flash reviews so each request stays within its prompt window."""
        check_batch_size = self._checks_per_request(backend, len(checks))
        assert check_batch_size > 0

        results: list[JudgeResult] = []
        for start in range(0, len(checks), check_batch_size):
            check_batch = checks[start : start + check_batch_size]
            batch_sheet = sheet
            if len(check_batch) < len(checks):
                questions = {check.id: sheet.questions[check.id] for check in check_batch}
                batch_sheet = Checksheet(sheet.name, sheet.state, questions)
            results.append(
                self._run_checksheet(
                    batch_sheet,
                    item_id,
                    item_label,
                    covered,
                    backend,
                    model,
                    checks=check_batch,
                )
            )

        if len(results) == 1:
            return results[0]
        statuses = [result.status for result in results]
        if "FAIL" in statuses:
            status = "FAIL"
        elif "WARN" in statuses:
            status = "WARN"
        elif all(result_status == "SKIPPED" for result_status in statuses):
            status = "SKIPPED"
        else:
            status = "PASS"
        counts = {value: statuses.count(value) for value in ("PASS", "WARN", "FAIL", "SKIPPED")}
        return JudgeResult(
            item_id=item_id,
            item_label=item_label,
            status=status,
            summary=(
                f"Reviewed {len(checks)} checks in {len(results)} bounded requests: "
                f"{counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL, "
                f"{counts['SKIPPED']} SKIPPED."
            ),
            issues=[issue for result in results for issue in result.issues],
            covered_files=covered,
            evaluations=[evaluation for result in results for evaluation in result.evaluations],
        )

    def _checks_per_request(self, backend: str, check_count: int) -> int:
        """Use one full request when Clef Flash has a larger configured context."""
        assert check_count > 0
        if backend != "clef-flash":
            return check_count
        backend_config = self.config.llm_judge.backends.get("clef-flash")
        context_window_tokens = (
            backend_config.context_window_tokens
            if backend_config and backend_config.context_window_tokens is not None
            else CLEF_FLASH_DEFAULT_CONTEXT_TOKENS
        )
        if context_window_tokens >= CLEF_FLASH_CONTEXT_BUDGET_BASELINE_TOKENS * 2:
            return check_count
        return min(CLEF_FLASH_CHECKS_PER_REQUEST, check_count)

    def _run_system_one_review(
        self,
        sheet: Checksheet,
        item_id: str,
        item_label: str,
        covered: list[str],
        backend: str,
        checks: list[LLMCheckRule],
        model: str | None,
    ) -> JudgeResult:
        """Classifies each typed decision review criterion into a review outcome."""
        backend_label = BACKEND_LABELS[backend]
        metric_label = "probability concentration" if backend == "clef-flash" else "confidence"
        if not checks:
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="SKIPPED",
                summary=f"No configured checks were available for {backend_label} review.",
                issues=[],
                covered_files=covered,
            )

        try:
            response = self._submit_checksheet(sheet, model, backend)
            answers = response["answers"]
            issues: list[dict] = []
            evaluations: list[JudgeEvaluation] = []
            decisions: list[str] = []
            outcome_counts = dict.fromkeys(REVIEW_OUTCOMES, 0)
            for check in checks:
                answer = answers.get(check.id)
                if not isinstance(answer, dict) or answer.get("type") != "choice":
                    raise ValueError(
                        f"{backend_label} response has no valid Choice answer for '{check.id}'"
                    )
                outcome = answer.get("choice")
                if outcome not in REVIEW_OUTCOMES:
                    raise ValueError(
                        f"{backend_label} returned an unknown review outcome for '{check.id}'"
                    )
                confidence = answer.get("confidence")
                if (
                    isinstance(confidence, bool)
                    or not isinstance(confidence, (int, float))
                    or not 0.0 <= confidence <= 1.0
                ):
                    raise ValueError(
                        f"{backend_label} returned an invalid {metric_label} for '{check.id}'"
                    )
                outcome_counts[outcome] += 1
                decisions.append(f"{check.id}={outcome}({metric_label} {confidence:.0%})")
                if outcome == "confirmed_violation":
                    severity = check.severity.upper()
                    if severity not in ("ERROR", "WARNING"):
                        severity = "WARNING"
                elif outcome in ("documented_open_issue", "improvement_suggestion"):
                    severity = "INFO"
                elif outcome == "no_issue":
                    severity = None
                else:
                    severity = "WARNING"
                evaluations.append(
                    JudgeEvaluation(
                        check_id=check.id,
                        classification=outcome,
                        confidence=float(confidence),
                        location=item_label,
                        severity=severity,
                    )
                )
                if outcome != "no_issue":
                    issues.append(
                        {
                            "severity": severity,
                            "check_id": check.id,
                            "location": item_label,
                            "classification": outcome,
                            "confidence": confidence,
                            "description": (
                                f"{backend_label} classified this criterion as '{outcome}' with "
                                f"{metric_label} {confidence:.0%}. The decision backend returns "
                                "no rationale or source location; inspect the linked section(s) manually."
                            ),
                        }
                    )

            has_error = any(issue["severity"] == "ERROR" for issue in issues)
            has_warning = any(issue["severity"] == "WARNING" for issue in issues)
            status = "FAIL" if has_error else ("WARN" if has_warning else "PASS")
            counts = ", ".join(f"{outcome}={count}" for outcome, count in outcome_counts.items())
            summary = (
                f"{backend_label} classified {len(checks)} typed review criteria: {counts}. "
                f"Selections: {', '.join(decisions)}. The decision backend does not generate "
                "explanations or citations."
            )
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status=status,
                summary=summary,
                issues=issues,
                evaluations=evaluations,
                covered_files=covered,
            )
        except LLMBackendError:
            raise
        except Exception as e:
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="FAIL",
                summary=f"{backend_label} review error: {e}",
                issues=[
                    {
                        "severity": "ERROR",
                        "check_id": "runtime_error",
                        "location": item_label,
                        "description": f"No usable {backend_label} verdict was returned: {e}",
                    }
                ],
                covered_files=covered,
            )


__all__ = ["UnifiedReviewEngine"]
