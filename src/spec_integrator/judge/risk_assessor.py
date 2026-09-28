from __future__ import annotations

from spec_integrator.judge.base import BaseJudge
from spec_integrator.judge.checksheet import Checksheet
from spec_integrator.models import (
    KeywordRiskAssessment,
    ParsedDocument,
    RiskAssessmentReport,
)


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
        if selected_backend not in ("jev", "mock"):
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

        def_texts = [
            self._retrieve_section_content(s, documents, max_chars=2000)
            for s in sg.get("defined_in", [])
        ]
        ref_texts = [
            self._retrieve_section_content(s, documents, max_chars=2000)
            for s in sg.get("referenced_in", [])
        ]

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
            if backend == "jev":
                sheet = Checksheet(
                    name="risk_assessment",
                    state={
                        "keyword": f"{{{keyword}}}",
                        "reference_count": len(sg.get("referenced_in", [])),
                        "definition_sections": def_texts or ["(No explicit definition section)"],
                        "referencing_sections": ref_texts or ["(No referencing sections)"],
                    },
                    questions={
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
                                "Very high: multiple severe failure modes or critical unresolved "
                                "assumptions.",
                            ],
                        },
                    },
                )
                response = self._submit_checksheet(sheet, model)
                answers = response["answers"]
                complexity_value = float(answers["complexity"]["score"])
                risk_value = float(answers["design_risk"]["score"])
                if not 0.0 <= complexity_value <= 4.0 or not 0.0 <= risk_value <= 4.0:
                    raise ValueError("Jev returned a score outside the configured 0-4 range")
                complexity_score = min(5, max(1, round(complexity_value) + 1))
                risk_score = min(5, max(1, round(risk_value) + 1))
                return KeywordRiskAssessment(
                    item_id=sg["item_id"],
                    keyword=keyword,
                    file_path=file_path,
                    tier=tier,
                    complexity_score=complexity_score,
                    risk_score=risk_score,
                    line=line,
                    covered_files=covered,
                    summary=(
                        f"Jev structured scores: complexity {complexity_value:.2f}/4, "
                        f"design risk {risk_value:.2f}/4. Jev does not provide a text rationale."
                    ),
                )
            raise ValueError(f"Unsupported checksheet backend: '{backend}'")
        except Exception as e:
            return KeywordRiskAssessment(
                item_id=sg["item_id"],
                keyword=keyword,
                file_path=file_path,
                tier=tier,
                complexity_score=3,
                risk_score=3,
                line=line,
                covered_files=covered,
                summary=f"Assessment error: {e}",
            )


__all__ = ["KeywordRiskAssessment", "RiskAssessmentReport", "RiskAssessor"]
