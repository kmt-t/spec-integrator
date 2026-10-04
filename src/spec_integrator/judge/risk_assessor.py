from __future__ import annotations

import json

from spec_integrator.judge.base import BaseJudge
from spec_integrator.judge.checksheet import Checksheet
from spec_integrator.judge.llm_backend import BACKEND_LABELS, SYSTEM_ONE_BACKENDS
from spec_integrator.models import (
    KeywordRiskAssessment,
    ParsedDocument,
    RiskAssessmentReport,
)

CLEF_FLASH_RISK_REQUEST_BUDGET_BYTES = 56 * 1024
CLEF_FLASH_DEFAULT_CONTEXT_TOKENS = 65536
CLEF_FLASH_CONTEXT_BUDGET_BASELINE_TOKENS = 8192
CLEF_FLASH_RISK_SECTION_CHAR_BUDGET = 500
CLEF_FLASH_CONTEXT_SECTIONS_PER_KIND = 4


class RiskAssessor(BaseJudge):
    """Scores each requirement/design keyword's complexity and design risk.

    A high risk_score is the signal consumed by the Obligation Gate
    to demand {VERIFY_LLM}.
    """

    def assess_subgraphs(
        self,
        subgraphs: list[dict],
        documents: list[ParsedDocument],
        backend: str | None = None,
        model: str | None = None,
        max_keywords: int = 15,
        exhaustive: bool = False,
        min_references: int = 0,
    ) -> RiskAssessmentReport:
        report = RiskAssessmentReport()
        selected_backend = backend or self.config.llm_judge.default_backend
        if selected_backend not in (*SYSTEM_ONE_BACKENDS, "mock"):
            raise ValueError(f"Unsupported checksheet backend: '{selected_backend}'")

        candidates: list[dict] = [
            sg for sg in subgraphs if len(sg.get("referenced_in", [])) >= min_references
        ]
        target_candidates = (
            candidates if (exhaustive or max_keywords <= 0) else candidates[:max_keywords]
        )

        print(
            f"Assessing complexity & design risk for {len(target_candidates)} candidate "
            f"keyword(s) using Backend: '{selected_backend}'..."
        )
        if exhaustive:
            print("  (Exhaustive mode: evaluating all keywords across all tiers)")

        for idx, sg in enumerate(target_candidates, start=1):
            print(
                f"  [{idx}/{len(target_candidates)}] Assessing '{sg['item_label']}'...",
                flush=True,
            )
            assessment = self._assess_single_keyword(
                sg, documents, backend=selected_backend, model=model
            )
            report.assessments.append(assessment)
            if assessment.risk_score >= self.config.obligation.risk_threshold:
                report.high_risk_count += 1

        report.total_evaluated = len(report.assessments)
        return report

    def _assess_single_keyword(
        self,
        sg: dict,
        documents: list[ParsedDocument],
        backend: str,
        model: str | None = None,
    ) -> KeywordRiskAssessment:
        keyword = str(sg["item_label"]).strip("{}")
        doc, sec = self._representative(sg, documents)
        tier = doc.tier if doc else "?"
        file_path = doc.file_path if doc else ""
        line = sec.line_start if sec else 1
        covered = self._covered_files(sg)
        definition_ids = list(sg.get("defined_in", []))
        reference_ids = list(sg.get("referenced_in", []))

        if backend == "mock":
            return KeywordRiskAssessment(
                item_id=sg["item_id"],
                keyword=keyword,
                file_path=file_path,
                tier=tier,
                complexity_score=3,
                risk_score=3,
                line=line,
                covered_files=covered,
                summary=f"Mock evaluation for '{{{keyword}}}'.",
            )

        try:
            if backend in SYSTEM_ONE_BACKENDS:
                backend_label = BACKEND_LABELS[backend]
                questions = self._risk_questions()
                context_note = ""
                if backend == "clef-flash":
                    state, context_note = self._fit_clef_flash_risk_context(
                        keyword=keyword,
                        model=model,
                        reference_count=len(reference_ids),
                        definition_ids=definition_ids,
                        reference_ids=reference_ids,
                        documents=documents,
                        questions=questions,
                    )
                else:
                    section_char_budget = self.config.llm_judge.section_char_budget
                    max_section_chars = (
                        min(2000, section_char_budget) if section_char_budget > 0 else 2000
                    )
                    def_texts = [
                        self._retrieve_section_content(s, documents, max_chars=max_section_chars)
                        for s in definition_ids
                    ]
                    ref_texts = [
                        self._retrieve_section_content(s, documents, max_chars=max_section_chars)
                        for s in reference_ids
                    ]
                    state = {
                        "keyword": f"{{{keyword}}}",
                        "reference_count": len(reference_ids),
                        "definition_sections": def_texts or ["(No explicit definition section)"],
                        "referencing_sections": ref_texts or ["(No referencing sections)"],
                    }

                sheet = Checksheet(name="risk_assessment", state=state, questions=questions)
                response = self._submit_checksheet(sheet, model, backend)
                answers = response["answers"]
                complexity_value = float(answers["complexity"]["score"])
                risk_value = float(answers["design_risk"]["score"])
                if not 0.0 <= complexity_value <= 4.0 or not 0.0 <= risk_value <= 4.0:
                    raise ValueError(
                        f"{backend_label} returned a score outside the configured 0-4 range"
                    )
                complexity_score = min(5, max(1, round(complexity_value) + 1))
                risk_score = min(5, max(1, round(risk_value) + 1))
                summary = (
                    f"{backend_label} structured scores: complexity {complexity_value:.2f}/4, "
                    f"design risk {risk_value:.2f}/4. The decision backend does not provide "
                    "a text rationale."
                )
                if context_note:
                    summary += f" {context_note}"
                return KeywordRiskAssessment(
                    item_id=sg["item_id"],
                    keyword=keyword,
                    file_path=file_path,
                    tier=tier,
                    complexity_score=complexity_score,
                    risk_score=risk_score,
                    line=line,
                    covered_files=covered,
                    summary=summary,
                )
            raise ValueError(f"Unsupported checksheet backend: '{backend}'")
        except Exception as e:
            raise RuntimeError(
                f"Risk assessment failed for '{{{keyword}}}' through backend '{backend}': {e}"
            ) from e

    @staticmethod
    def _sample_section_ids(section_ids: list[str]) -> list[str]:
        """Selects an evenly spaced sample while retaining document coverage."""
        limit = CLEF_FLASH_CONTEXT_SECTIONS_PER_KIND
        if len(section_ids) <= limit:
            return section_ids
        indexes = {round(index * (len(section_ids) - 1) / (limit - 1)) for index in range(limit)}
        return [section_ids[index] for index in sorted(indexes)]

    @staticmethod
    def _risk_questions() -> dict[str, dict]:
        return {
            "complexity": {
                "type": "score",
                "instructions": (
                    "Rate the inherent implementation complexity of this requirement "
                    "or design keyword using its definition and referencing sections."
                ),
                "criteria": [
                    "Very low: local, straightforward behavior with few states.",
                    "Low: limited interactions and uncomplicated implementation.",
                    "Moderate: several interacting behaviors or notable constraints.",
                    "High: large state space, asynchronous behavior, ownership transfer, "
                    "cache lifecycle, or low-level hardware interaction.",
                    "Very high: several high-complexity factors combine or require "
                    "extensive coordination.",
                ],
            },
            "design_risk": {
                "type": "score",
                "instructions": (
                    "Rate the inherent design risk using the definition and referencing "
                    "sections. Consider deadlocks, races, memory corruption, starvation, "
                    "failure handling, and ambiguous assumptions."
                ),
                "criteria": [
                    "Very low: behavior is explicit and has few consequential failure modes.",
                    "Low: limited failure exposure with clear handling.",
                    "Moderate: meaningful failure modes or assumptions require verification.",
                    "High: substantial risk of deadlock, race, corruption, starvation, or "
                    "missing recovery.",
                    "Very high: multiple severe failure modes or critical unresolved assumptions.",
                ],
            },
        }

    def _fit_clef_flash_risk_context(
        self,
        keyword: str,
        model: str | None,
        reference_count: int,
        definition_ids: list[str],
        reference_ids: list[str],
        documents: list[ParsedDocument],
        questions: dict[str, dict],
    ) -> tuple[dict, str]:
        """Fits a sampled, configured-length risk context under Ollama's 64 KiB limit."""
        backend_config = self.config.llm_judge.backends.get("clef-flash")
        selected_model = model or (
            backend_config.model if backend_config and backend_config.model else "clef-flash"
        )
        definition_sections = self._sample_section_ids(definition_ids)
        referencing_sections = self._sample_section_ids(reference_ids)
        configured_budget = self.config.llm_judge.section_char_budget
        context_window_tokens = (
            backend_config.context_window_tokens
            if backend_config and backend_config.context_window_tokens is not None
            else CLEF_FLASH_DEFAULT_CONTEXT_TOKENS
        )
        if context_window_tokens < 1:
            raise ValueError("Clef Flash context_window_tokens must be positive")
        context_section_budget = (
            CLEF_FLASH_RISK_SECTION_CHAR_BUDGET
            * context_window_tokens
            // CLEF_FLASH_CONTEXT_BUDGET_BASELINE_TOKENS
        )
        section_char_limit = (
            min(context_section_budget, configured_budget)
            if configured_budget > 0
            else context_section_budget
        )

        while section_char_limit > 0:
            definition_texts = [
                self._retrieve_section_content(section_id, documents, section_char_limit)
                for section_id in definition_sections
            ]
            referencing_texts = [
                self._retrieve_section_content(section_id, documents, section_char_limit)
                for section_id in referencing_sections
            ]
            section_truncated = any(
                "[TRUNCATED:" in text for text in definition_texts + referencing_texts
            )
            omitted_sections = len(definition_sections) < len(definition_ids) or len(
                referencing_sections
            ) < len(reference_ids)
            context_note = ""
            if omitted_sections or section_truncated:
                context_note = (
                    f"Context was limited to fit Clef Flash's {context_window_tokens:,}-token context "
                    "and Ollama's "
                    "64 KiB request body: included "
                    f"{len(definition_sections)} of {len(definition_ids)} definition sections and "
                    f"{len(referencing_sections)} of {len(reference_ids)} referencing sections; "
                    f"each section is capped at {section_char_limit} characters."
                )
            state = {
                "keyword": f"{{{keyword}}}",
                "reference_count": reference_count,
                "definition_sections": definition_texts or ["(No explicit definition section)"],
                "referencing_sections": referencing_texts or ["(No referencing sections)"],
            }
            if context_note:
                state["context_limit_note"] = context_note
            payload = {"model": selected_model, "state": state, "questions": questions}
            body_size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
            if body_size <= CLEF_FLASH_RISK_REQUEST_BUDGET_BYTES:
                return state, context_note
            section_char_limit = max(1, section_char_limit - 100)

        raise ValueError("Risk-assessment checksheet cannot be reduced below Clef Flash's request limit.")


__all__ = ["KeywordRiskAssessment", "RiskAssessmentReport", "RiskAssessor"]
