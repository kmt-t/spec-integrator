from __future__ import annotations

from spec_integrator.config import Config
from spec_integrator.graph import DocumentIsland
from spec_integrator.judge.base import BaseJudge
from spec_integrator.judge.unified_reviewer import UnifiedReviewEngine
from spec_integrator.models import JudgeReport, JudgeResult, ParsedDocument


class SemanticJudge(BaseJudge):
    """Orchestrates LLM as a Judge audits across connected document islands
    and individual specification documents using UnifiedReviewEngine.
    """

    def __init__(self, config: Config):
        super().__init__(config)
        self.unified_engine = UnifiedReviewEngine(config)

    def judge_islands(
        self,
        islands: list[DocumentIsland],
        documents: list[ParsedDocument],
        backend: str | None = None,
        model: str | None = None,
        max_islands: int = 10,
        check_ids: list[str] | None = None,
    ) -> JudgeReport:
        """Audits document clusters (islands) with all mutually referencing documents side-by-side."""
        report = JudgeReport()
        selected_backend = backend or self.config.llm_judge.default_backend
        target_islands = islands[:max_islands] if max_islands > 0 else islands

        print(
            f"Auditing {len(target_islands)} connected island(s) using LLM Backend: '{selected_backend}'..."
        )
        for idx, isl in enumerate(target_islands, start=1):
            print(
                f"  [{idx}/{len(target_islands)}] Evaluating Island '{isl.name}' ({isl.total_docs} docs, {isl.total_sections} sections)...",
                flush=True,
            )
            res = self.unified_engine.review_document_island(
                isl, documents, backend=selected_backend, model=model, check_ids=check_ids
            )
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
        """Audits individual documents internally for consistency, clarity, and standards."""
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
            res = self.unified_engine.review_single_document(
                doc, backend=selected_backend, model=model
            )
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


__all__ = ["JudgeReport", "JudgeResult", "SemanticJudge"]
