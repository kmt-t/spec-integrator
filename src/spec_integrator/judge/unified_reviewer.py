from __future__ import annotations

import time
from dataclasses import dataclass

from spec_integrator.config import Config, LLMCheckRule
from spec_integrator.graph import KeywordGroup
from spec_integrator.judge.base import BaseJudge
from spec_integrator.models import JudgeEvaluation, JudgeResult, ParsedDocument, ParsedSection


@dataclass(frozen=True)
class _KeywordLinkPair:
    keyword: str
    item_id: str
    item_label: str
    definition: tuple[ParsedDocument, ParsedSection] | None
    reference: tuple[ParsedDocument, ParsedSection]
    registry_entry: dict[str, str] | None
    declared_source: str


class UnifiedReviewEngine(BaseJudge):
    """Unified engine for all LLM document audits:

    - Single document internal review
    - Definition/reference keyword link-pair review
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
        """Dynamically assembles the review prompt for single-section or link-pair mode."""
        lines: list[str] = [
            "You are a strict, formal System Specification Verification Judge and Auditor.",
            f"Your mission is to perform an exhaustive, evidence-based audit for: {target_name}",
            f"Audit Mode: {'[SINGLE SECTION REVIEW]' if mode == 'single' else '[KEYWORD LINK-PAIR REVIEW]'}",
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
        results: list[JudgeResult] = []
        print(f"Reviewing {len(pairs)} definition/reference link pair(s)...", flush=True)
        for index, pair in enumerate(pairs, start=1):
            print(f"  [{index}/{len(pairs)}] {pair.item_label}", flush=True)
            context_text = self._keyword_link_pair_context(pair)
            prompt = self.assemble_prompt(
                "link_pair",
                pair.item_label,
                context_text,
                checks,
                extra_instructions=(
                    "Evaluate only this one definition/reference link pair. Do not infer or report "
                    "relationships with other sections in the keyword group. Keep every finding at "
                    "the section level; do not report sentence-level locations. Cite the implicated "
                    "definition or reference section by file and heading."
                ),
            )
            if dry_run:
                print(
                    f"=== DRY-RUN LINK-PAIR PROMPT [{index}/{len(pairs)}]: "
                    f"{pair.item_label} ({pair.item_id}) ==="
                )
                print(prompt)
                print("=" * 80)
                result = JudgeResult(
                    item_id=pair.item_id,
                    item_label=pair.item_label,
                    status="PASS",
                    summary="[Dry Run] Definition/reference link-pair prompt generated.",
                    covered_files=self._pair_covered_files(pair),
                )
            else:
                result = self._run_judge_llm(
                    prompt,
                    pair.item_id,
                    pair.item_label,
                    self._pair_covered_files(pair),
                    selected_backend,
                    model,
                    checks=checks,
                    context_text=context_text,
                    mode="link_pair",
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
        registry_entry = self._keyword_registry_entry(documents, group.keyword)
        declared_source = (
            registry_entry["definition_source"].strip("`").replace("\\", "/")
            if registry_entry
            else ""
        )
        source_suffixes = {declared_source}
        if declared_source.startswith("docs/"):
            source_suffixes.add(declared_source.removeprefix("docs/"))
        definition_docs = [
            document
            for document in documents
            if any(
                document.file_path == source or document.file_path.endswith(f"/{source}")
                for source in source_suffixes
            )
        ]
        if declared_source and not definition_docs:
            basename_matches = [
                document
                for document in documents
                if document.file_path.rsplit("/", 1)[-1] == declared_source.rsplit("/", 1)[-1]
            ]
            if len(basename_matches) == 1:
                definition_docs = basename_matches
        definitions = sorted(
            [
                (document, section)
                for document in definition_docs
                for section in document.sections
                if group.keyword in section.keywords
            ],
            key=lambda pair: (pair[0].file_path, pair[1].line_start, pair[1].section_id),
        )
        definition_section_ids = {section.section_id for _document, section in definitions}
        references = sorted(
            [
                (document, section)
                for document, section in linked_sections
                if section.section_id not in definition_section_ids
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
                        registry_entry=registry_entry,
                        declared_source=declared_source,
                    )
                )
        return pairs

    def _keyword_link_pair_context(self, pair: _KeywordLinkPair) -> str:
        lines = [
            f"Keyword: {{{pair.keyword}}}",
            "Evaluation unit: one definition section paired with one reference section.",
            f"Declared definition source: {pair.declared_source or '(not found in keyword registry)'}",
            "Definition source metadata is read from docs/architecture/keyword_dictionary.md.",
            "Definitions use the keyword inline in their source section; references use the "
            "section-level <!-- traceability: {Keyword} --> comment.",
            "",
        ]
        if pair.registry_entry:
            lines.extend(
                [
                    "### KEYWORD REGISTRY ENTRY",
                    f"- Target component: {pair.registry_entry['target_component']}",
                    f"- Summary: {pair.registry_entry['summary']}",
                    "",
                ]
            )

        def append_section(
            role: str, section_pair: tuple[ParsedDocument, ParsedSection] | None
        ) -> None:
            lines.append(f"### {role} SECTION")
            if section_pair is None:
                lines.extend(["(not found in the declared definition source)", ""])
                return
            document, section = section_pair
            keywords = f" [Keywords: {', '.join(section.keywords)}]" if section.keywords else ""
            lines.extend(
                [
                    f"File: {document.file_path} (Tier: {document.tier})",
                    f"Section: {section.heading} (ID: {section.section_id}; "
                    f"lines {section.line_start}-{section.line_end}){keywords}",
                    self._budgeted(section.body_text),
                    "",
                ]
            )

        append_section("DEFINITION", pair.definition)
        append_section("REFERENCE", pair.reference)
        return "\n".join(lines)

    @staticmethod
    def _pair_covered_files(pair: _KeywordLinkPair) -> list[str]:
        files = [pair.reference[0].file_path]
        if pair.definition:
            files.insert(0, pair.definition[0].file_path)
        return list(dict.fromkeys(files))

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
