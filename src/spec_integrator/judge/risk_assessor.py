from __future__ import annotations

from spec_integrator.config import Config
from spec_integrator.judge.base import BaseJudge
from spec_integrator.models import (
    KeywordRiskAssessment,
    ParsedDocument,
    RiskAssessmentReport,
)

ASSESS_PROMPT_TEMPLATE = """You are a Principal Embedded Systems Architect and Formal Verification Expert.
Score the complexity and design risk of the following requirement/design keyword, based on its
definition and how it is used across the specification. Score only -- do not recommend a
verification method; that decision belongs to the author, not this triage.

Keyword: {{{item_label}}}
Referenced in {ref_count} section(s).

=== DEFINITION ===
{definition_texts}

=== REFERENCING CONTEXT ===
{referencing_texts}

=== EVALUATION CRITERIA ===
1. Complexity (1-5): State space size, asynchronous/coroutine behavior, zero-copy ownership
   transfer, cache lifecycle, low-level hardware interaction.
2. Design Risk (1-5): Deadlock, race condition, memory corruption, starvation, unhandled
   failure modes, ambiguous/unspecified assumptions, missing error recovery.

=== OUTPUT FORMAT ===
Respond ONLY with a valid JSON object in English:
```json
{{
  "complexity_score": 1 to 5,
  "risk_score": 1 to 5,
  "summary": "One-sentence justification in English"
}}
```
"""


class RiskAssessor(BaseJudge):
    """Scores each requirement/design keyword's complexity and design risk.

    A high risk_score is the signal consumed by the Obligation Gate
    to demand {VERIFY_LLM}.
    """

    def __init__(self, config: Config):
        super().__init__(config)

    def assess_subgraphs(
        self,
        subgraphs: list[dict],
        documents: list[ParsedDocument],
        backend: str | None = None,
        model: str | None = None,
        max_keywords: int = 15,
        exhaustive: bool = False,
        min_references: int = 0,
        include_meta: bool = False,
        include_reqs: bool = False,
        target_tiers: list[int | str] | None = None,
    ) -> RiskAssessmentReport:
        report = RiskAssessmentReport()
        selected_backend = backend or self.config.llm_judge.default_backend

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

        prompt = ASSESS_PROMPT_TEMPLATE.format(
            item_label=keyword,
            ref_count=len(sg.get("referenced_in", [])),
            definition_texts="\n\n".join(t for t in def_texts if t)
            or "(No explicit definition section)",
            referencing_texts="\n\n".join(t for t in ref_texts if t) or "(No referencing sections)",
        )

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
            if backend == "sakura":
                raw_resp = self._call_sakura(prompt, model)
            elif backend == "openrouter":
                raw_resp = self._call_openrouter(prompt, model)
            elif backend == "ollama":
                raw_resp = self._call_ollama(prompt, model)
            else:
                raw_resp = self._call_sakura(prompt, model)

            parsed = self._extract_json(raw_resp)
            return KeywordRiskAssessment(
                item_id=sg["item_id"],
                keyword=keyword,
                file_path=file_path,
                tier=tier,
                complexity_score=int(parsed.get("complexity_score", 3)),
                risk_score=int(parsed.get("risk_score", 3)),
                line=line,
                covered_files=covered,
                summary=str(parsed.get("summary", "")),
            )
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
