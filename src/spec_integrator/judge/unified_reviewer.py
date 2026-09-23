from __future__ import annotations

import time

from spec_integrator.config import Config, LLMCheckRule
from spec_integrator.graph import DocumentIsland
from spec_integrator.judge.base import BaseJudge
from spec_integrator.models import JudgeEvaluation, JudgeResult, ParsedDocument, ParsedSection


class UnifiedReviewEngine(BaseJudge):
    """Unified engine for all LLM document audits:

    - Single document internal review
    - Multi-document connected island review
    - Modular, configurable check rules defined strictly in configuration
    """

    def __init__(self, config: Config):
        super().__init__(config)

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

    def assemble_prompt(
        self,
        mode: str,
        target_name: str,
        sections_context: str,
        checks: list[LLMCheckRule],
        extra_instructions: str = "",
    ) -> str:
        """Dynamically assembles the review prompt for single-doc or island mode."""
        lines: list[str] = [
            "You are a strict, formal System Specification Verification Judge and Auditor.",
            f"Your mission is to perform an exhaustive, evidence-based audit for: {target_name}",
            f"Audit Mode: {'[SINGLE SECTION REVIEW]' if mode == 'single' else '[KEYWORD ISLAND REVIEW]'}",
            "",
            "=== SPECIFICATION CONTENT TO AUDIT ===",
            sections_context,
            "",
            "=== EVALUATION CRITERIA ===",
            f"Perform your audit systematically against the following {len(checks)} evaluation rule(s):",
            "",
        ]

        config_dir = self.config.config_dir
        for idx, rule in enumerate(checks, start=1):
            rule_text = rule.get_prompt_text(config_dir)
            lines.append(f"{idx}. [{rule.id}] {rule.name} (Severity: {rule.severity}):")
            for sub_line in rule_text.splitlines():
                lines.append(f"   {sub_line}")
            lines.append("")

        if extra_instructions:
            lines.append("=== ADDITIONAL INSTRUCTIONS ===")
            lines.append(extra_instructions)
            lines.append("")

        lines.extend(
            [
                "=== AUDITOR RULES ===",
                "- Literal Evaluation: Judge what the text actually and explicitly states, not what it might have intended.",
                "- No Vacuous Confirmation: Restating a claim back as confirmation is not an audit.",
                "- Specific Citations: When reporting contradictions, duplicates, or missing citations, always cite the specific file and section heading.",
                "- Accurate Rule Tagging: Always tag each reported issue with the corresponding 'check_id' from the evaluation criteria.",
                "- No False Positives: If the text meets all criteria, state so concisely as PASS; do not manufacture non-existent issues.",
                "",
                "=== OUTPUT FORMAT ===",
                "Respond ONLY with a valid JSON object in English in the following format:",
                "```json",
                "{",
                '  "status": "PASS" | "WARN" | "FAIL",',
                '  "summary": "Concise explanation of the evaluation result in English",',
                '  "issues": [',
                "    {",
                '      "severity": "ERROR" | "WARNING",',
                '      "check_id": "rule_id_from_criteria",',
                '      "location": "File or Section name",',
                '      "description": "Detailed explanation of contradiction, unbacked claim, or ambiguity in English"',
                "    }",
                "  ]",
                "}",
                "```",
            ]
        )
        return "\n".join(lines)

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
            context_lines.extend(["", self._budgeted(sec.body_text)])
            context_text = "\n".join(context_lines)
            prompt = self.assemble_prompt("single", section_ref, context_text, checks)

            if dry_run:
                print(f"=== DRY-RUN PROMPT FOR SECTION: {section_ref} ===")
                print(prompt)
                print("=" * 80)
                result = JudgeResult(
                    item_id=f"{doc.file_path}::{sec.section_id}",
                    item_label=section_ref,
                    status="PASS",
                    summary="[Dry Run] Section prompt generated successfully.",
                    covered_files=[doc.file_path],
                )
            else:
                result = self._run_judge_llm(
                    prompt,
                    f"{doc.file_path}::{sec.section_id}",
                    section_ref,
                    [doc.file_path],
                    selected_backend,
                    model,
                    checks=checks,
                    context_text=context_text,
                    mode="single",
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
            summary = f"[Dry Run] Generated {len(section_results)} section-level prompts."

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

    def review_document_island(
        self,
        island: DocumentIsland,
        documents: list[ParsedDocument],
        backend: str | None = None,
        model: str | None = None,
        check_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> JudgeResult:
        """Reviews the declared definition and linked reference sections of a keyword island."""
        checks = self.get_effective_checks("cluster", check_ids=check_ids)
        if not checks:
            return JudgeResult(
                item_id=island.island_id,
                item_label=island.name,
                status="SKIPPED",
                summary="No active checks configured for cluster island review.",
                covered_files=island.file_paths,
            )

        # Keep the shared-keyword definition and reference sections separate.
        linked_section_ids = set(island.section_ids)
        doc_map = {d.file_path: d for d in documents}
        linked_docs = []
        for file_path in island.file_paths:
            doc = doc_map.get(file_path)
            if doc and any(sec.section_id in linked_section_ids for sec in doc.sections):
                linked_docs.append(doc)
        linked_sections = [
            (doc, sec)
            for doc in linked_docs
            for sec in doc.sections
            if sec.section_id in linked_section_ids
        ]
        if not linked_sections:
            return JudgeResult(
                item_id=island.island_id,
                item_label=island.name,
                status="SKIPPED",
                summary="No linked sections were available for island review.",
                issues=[],
                covered_files=[],
            )

        registry_entry = self._keyword_registry_entry(documents, island.name)
        declared_source = (
            registry_entry["definition_source"].strip("`").replace("\\", "/")
            if registry_entry
            else ""
        )
        exact_definition_docs = [
            doc
            for doc in documents
            if declared_source
            and (doc.file_path == declared_source or doc.file_path.endswith(f"/{declared_source}"))
        ]
        if declared_source and not exact_definition_docs and "/" not in declared_source:
            exact_definition_docs = [
                doc for doc in documents if doc.file_path.rsplit("/", 1)[-1] == declared_source
            ]
        definition_sections = [
            (doc, sec)
            for doc in exact_definition_docs
            for sec in doc.sections
            if island.name in sec.keywords
        ]
        definition_section_ids = {sec.section_id for _doc, sec in definition_sections}
        reference_sections = [
            (doc, sec)
            for doc, sec in linked_sections
            if sec.section_id not in definition_section_ids
        ]
        covered_files = list(
            dict.fromkeys(
                doc.file_path for doc, _section in [*definition_sections, *reference_sections]
            )
        )
        context_blocks: list[str] = [
            f"Keyword Island: {island.name}",
            f"Declared definition source: {declared_source or '(not found in keyword registry)'}",
            (
                f"Definition sections: {len(definition_sections)}; "
                f"reference sections: {len(reference_sections)}"
            ),
            f"Covered Files: {', '.join(covered_files)}",
            f"Shared Keyword: {', '.join(island.keywords) if island.keywords else island.name}",
            "Definition source metadata is read from docs/architecture/keyword_dictionary.md.",
            "Project rule: definitions use the keyword inline in their source section; references "
            "use a section-level <!-- traceability: {Keyword} --> comment.",
            "",
        ]

        if registry_entry:
            context_blocks.extend(
                [
                    "### KEYWORD REGISTRY ENTRY",
                    f"- Target component: {registry_entry['target_component']}",
                    f"- Summary: {registry_entry['summary']}",
                    "",
                ]
            )

        def append_sections(
            role: str, sections: list[tuple[ParsedDocument, ParsedSection]]
        ) -> None:
            context_blocks.append(f"### {role} SECTIONS")
            if not sections:
                context_blocks.append("(none found)")
                context_blocks.append("")
                return
            for doc, sec in sections:
                keywords = f" [Keywords: {', '.join(sec.keywords)}]" if sec.keywords else ""
                context_blocks.append(
                    f"--- {role}: {doc.file_path} :: {sec.heading} "
                    f"(Tier: {doc.tier}; ID: {sec.section_id}; "
                    f"lines {sec.line_start}-{sec.line_end}){keywords} ---"
                )
                context_blocks.append(self._budgeted(sec.body_text))
                context_blocks.append("")

        append_sections("DEFINITION", definition_sections)
        append_sections("REFERENCE", reference_sections)

        context_text = "\n\n".join(context_blocks)
        prompt = self.assemble_prompt("cluster", island.name, context_text, checks)

        if dry_run:
            print(f"=== DRY-RUN PROMPT FOR ISLAND: {island.name} ({island.island_id}) ===")
            print(prompt)
            print("=" * 80)
            return JudgeResult(
                item_id=island.island_id,
                item_label=island.name,
                status="PASS",
                summary="[Dry Run] Prompt generated successfully.",
                covered_files=covered_files,
            )

        selected_backend = backend or self.config.llm_judge.default_backend
        result = self._run_judge_llm(
            prompt,
            island.island_id,
            island.name,
            covered_files,
            selected_backend,
            model,
            checks=checks,
            context_text=context_text,
            mode="cluster",
        )
        return result

    def _run_judge_llm(
        self,
        prompt: str,
        item_id: str,
        item_label: str,
        covered: list[str],
        backend: str,
        model: str | None,
        checks: list[LLMCheckRule] | None = None,
        context_text: str = "",
        mode: str = "single",
    ) -> JudgeResult:
        """Executes prompt against backend and parses structured JSON verdict."""
        if backend == "mock":
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="PASS",
                summary=f"Mock evaluation passed for '{item_label}'.",
                issues=[],
                covered_files=covered,
            )
        if backend == "jev":
            return self._run_jev_review(
                item_id,
                item_label,
                covered,
                checks or [],
                context_text,
                mode,
                model,
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
                        "check_id": "runtime_error",
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

    def _run_jev_review(
        self,
        item_id: str,
        item_label: str,
        covered: list[str],
        checks: list[LLMCheckRule],
        context_text: str,
        mode: str,
        model: str | None,
    ) -> JudgeResult:
        """Classifies each Jev review criterion into a review outcome."""
        if not checks:
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="SKIPPED",
                summary="No configured checks were available for Jev review.",
                issues=[],
                covered_files=covered,
            )

        outcome_options = {
            "confirmed_violation": (
                "A specific, unmistakable defect or contradiction is directly visible in the "
                "supplied section(s), and the criterion clearly applies."
            ),
            "possible_violation": (
                "Specific evidence suggests a defect, but interpretation or a missing link means "
                "a human should confirm it before treating it as a violation."
            ),
            "documented_open_issue": (
                "The supplied text explicitly marks the matter as unresolved, under study, or out "
                "of scope; that status alone is not a defect unless the text contradicts it."
            ),
            "insufficient_context": (
                "The supplied section(s) do not contain enough information to judge this criterion, "
                "and no direct violation is visible in the available text."
            ),
            "improvement_suggestion": (
                "The text meets the criterion, but a non-blocking style or clarity improvement "
                "could be suggested."
            ),
            "no_issue": (
                "No concrete violation, unresolved-status concern, context gap, or useful "
                "improvement is supported by the supplied text."
            ),
        }
        questions: dict[str, dict] = {}
        for check in checks:
            questions[check.id] = {
                "type": "choice",
                "instructions": (
                    "Using only `specification_content`, classify the evidence for this review "
                    f"criterion: {check.name}. Select exactly one best-fitting outcome. Treat the "
                    "outcomes as mutually exclusive. Do not infer missing requirements, intended "
                    "wording, or defects from common practice or absent context. Ignore valid "
                    "technical identifiers. A matter explicitly marked unresolved is not itself "
                    "a violation. Prefer `possible_violation` or `insufficient_context` over "
                    "`confirmed_violation` whenever the evidence is ambiguous. "
                    f"Criterion instructions: {check.get_prompt_text(self.config.config_dir)}"
                ),
                "criteria": outcome_options,
            }

        try:
            response = self._call_jev(
                {
                    "review_target": item_label,
                    "review_mode": mode,
                    "covered_files": covered,
                    "specification_content": context_text,
                },
                questions,
                model,
            )
            answers = response["answers"]
            issues: list[dict] = []
            evaluations: list[JudgeEvaluation] = []
            decisions: list[str] = []
            outcome_counts = dict.fromkeys(outcome_options, 0)
            for check in checks:
                answer = answers.get(check.id)
                if not isinstance(answer, dict) or answer.get("type") != "choice":
                    raise ValueError(f"Jev response has no valid Choice answer for '{check.id}'")
                outcome = answer.get("choice")
                if outcome not in outcome_options:
                    raise ValueError(f"Jev returned an unknown review outcome for '{check.id}'")
                confidence = answer.get("confidence")
                if (
                    isinstance(confidence, bool)
                    or not isinstance(confidence, (int, float))
                    or not 0.0 <= confidence <= 1.0
                ):
                    raise ValueError(f"Jev returned invalid confidence for '{check.id}'")
                outcome_counts[outcome] += 1
                decisions.append(f"{check.id}={outcome}({confidence:.0%})")
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
                                f"Jev classified this criterion as '{outcome}' with "
                                f"{confidence:.0%} confidence. Jev returns no rationale or source "
                                "location; inspect the linked section(s) manually."
                            ),
                        }
                    )

            has_error = any(issue["severity"] == "ERROR" for issue in issues)
            has_warning = any(issue["severity"] == "WARNING" for issue in issues)
            status = "FAIL" if has_error else ("WARN" if has_warning else "PASS")
            counts = ", ".join(f"{outcome}={count}" for outcome, count in outcome_counts.items())
            summary = (
                f"Jev classified {len(checks)} typed review criteria: {counts}. "
                f"Selections: {', '.join(decisions)}. Jev does not generate explanations or citations."
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
        except Exception as e:
            return JudgeResult(
                item_id=item_id,
                item_label=item_label,
                status="FAIL",
                summary=f"Jev review error: {e}",
                issues=[
                    {
                        "severity": "ERROR",
                        "check_id": "runtime_error",
                        "location": item_label,
                        "description": f"No usable Jev verdict was returned: {e}",
                    }
                ],
                covered_files=covered,
            )

    @staticmethod
    def _keyword_registry_entry(
        documents: list[ParsedDocument], keyword: str
    ) -> dict[str, str] | None:
        """Returns the registry's definition source and summary for one keyword."""
        registry = next(
            (
                doc
                for doc in documents
                if doc.file_path.endswith("architecture/keyword_dictionary.md")
            ),
            None,
        )
        if registry is None:
            return None

        expected_label = f"{{{keyword}}}"
        for line in registry.content.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 4 or cells[0].strip("`") != expected_label:
                continue
            return {
                "definition_source": cells[1],
                "target_component": cells[2],
                "summary": cells[3],
            }
        return None


__all__ = ["UnifiedReviewEngine"]
