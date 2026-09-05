from __future__ import annotations

import time

from spec_integrator.config import Config, LLMCheckRule
from spec_integrator.graph import DocumentIsland
from spec_integrator.judge.base import BaseJudge
from spec_integrator.models import JudgeResult, ParsedDocument


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
            f"Audit Mode: {'[SINGLE DOCUMENT REVIEW]' if mode == 'single' else '[ISLAND CROSS-DOCUMENT REVIEW]'}",
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
        """Reviews an individual document's sections for internal consistency and standards."""
        checks = self.get_effective_checks("single", check_ids=check_ids)
        if not checks:
            return JudgeResult(
                item_id=doc.file_path,
                item_label=doc.file_path,
                status="SKIPPED",
                summary="No active checks configured for single document review.",
                covered_files=[doc.file_path],
            )

        # Build section context
        sec_blocks: list[str] = [f"File: {doc.file_path} (Tier: {doc.tier})"]
        for sec in doc.sections:
            body = self._budgeted(sec.body_text)
            sec_blocks.append(f"--- Section: {sec.heading} (Line {sec.line_start}) ---\n{body}")

        context_text = "\n\n".join(sec_blocks)
        prompt = self.assemble_prompt("single", doc.file_path, context_text, checks)

        if dry_run:
            print(f"=== DRY-RUN PROMPT FOR SINGLE DOC: {doc.file_path} ===")
            print(prompt)
            print("=" * 80)
            return JudgeResult(
                item_id=doc.file_path,
                item_label=doc.file_path,
                status="PASS",
                summary="[Dry Run] Prompt generated successfully.",
                covered_files=[doc.file_path],
            )

        selected_backend = backend or self.config.llm_judge.default_backend
        return self._run_judge_llm(
            prompt,
            doc.file_path,
            doc.file_path,
            [doc.file_path],
            selected_backend,
            model,
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
        """Reviews all mutually connected documents and sections within an island side-by-side."""
        checks = self.get_effective_checks("cluster", check_ids=check_ids)
        if not checks:
            return JudgeResult(
                item_id=island.island_id,
                item_label=island.name,
                status="SKIPPED",
                summary="No active checks configured for cluster island review.",
                covered_files=island.file_paths,
            )

        # Build structured multi-document context
        context_blocks: list[str] = [
            f"Island: {island.name} ({island.total_docs} documents, {island.total_sections} sections)",
            f"Covered Files: {', '.join(island.file_paths)}",
            f"Shared Keywords: {', '.join(island.keywords) if island.keywords else '(Direct links)'}",
            "",
        ]

        doc_map = {d.file_path: d for d in documents}
        for fpath in island.file_paths:
            doc = doc_map.get(fpath)
            if not doc:
                continue
            context_blocks.append("########################################")
            context_blocks.append(f"### FILE: {doc.file_path} (Tier {doc.tier})")
            context_blocks.append("########################################")
            for sec in doc.sections:
                kws_str = f" [Keywords: {', '.join(sec.keywords)}]" if sec.keywords else ""
                body = self._budgeted(sec.body_text)
                context_blocks.append(
                    f"--- Section: {sec.heading} (Line {sec.line_start}){kws_str} ---\n{body}"
                )

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
                covered_files=island.file_paths,
            )

        selected_backend = backend or self.config.llm_judge.default_backend
        return self._run_judge_llm(
            prompt,
            island.island_id,
            island.name,
            island.file_paths,
            selected_backend,
            model,
        )

    def _run_judge_llm(
        self,
        prompt: str,
        item_id: str,
        item_label: str,
        covered: list[str],
        backend: str,
        model: str | None,
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


__all__ = ["UnifiedReviewEngine"]
