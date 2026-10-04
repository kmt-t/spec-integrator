from __future__ import annotations

import json
from typing import TYPE_CHECKING

from spec_integrator.judge.checksheet import Checksheet, submit_checksheet
from spec_integrator.judge.llm_backend import BACKEND_LABELS, SYSTEM_ONE_BACKENDS
from spec_integrator.models import VerificationIssue

if TYPE_CHECKING:
    from spec_integrator.config import Config
    from spec_integrator.db import DocAuditDB


class TermVarianceJudge:
    """Uses an LLM to evaluate whether semantically similar terms represent undesirable term variance."""

    def __init__(self, config: Config):
        self.config = config

    def judge_similar_pairs(
        self,
        db: DocAuditDB,
        backend: str | None = None,
        model: str | None = None,
        max_pairs: int = 20,
    ) -> int:
        """Evaluates high-similarity pairs with LLM context check and records variance judgments."""
        used_backend = backend or self.config.llm_judge.default_backend
        if used_backend not in (*SYSTEM_ONE_BACKENDS, "mock"):
            raise ValueError(f"Unsupported checksheet backend: '{used_backend}'")
        similarities = db.get_term_similarities()

        unjudged_pairs = [
            row
            for row in similarities
            if not db.is_similarity_judged(row["term_a"], row["term_b"], backend=used_backend)
        ]

        if max_pairs > 0:
            unjudged_pairs = unjudged_pairs[:max_pairs]

        if not unjudged_pairs:
            return 0

        judged_count = 0
        for row in unjudged_pairs:
            term_a = row["term_a"]
            term_b = row["term_b"]

            kw_a = db.get_term_keyword(term_a)
            kw_b = db.get_term_keyword(term_b)
            if not kw_a or not kw_b:
                continue

            try:
                occs_a = json.loads(kw_a["occurrences_json"]) if kw_a["occurrences_json"] else []
                occs_b = json.loads(kw_b["occurrences_json"]) if kw_b["occurrences_json"] else []
            except Exception:
                continue

            if not occs_a or not occs_b:
                continue

            occ_a = occs_a[0]
            occ_b = occs_b[0]

            try:
                if used_backend in SYSTEM_ONE_BACKENDS:
                    backend_label = BACKEND_LABELS[used_backend]
                    metric_label = (
                        "probability concentration" if used_backend == "clef-flash" else "confidence"
                    )
                    sheet = Checksheet(
                        name="term_variance",
                        state={
                            "term_a": term_a,
                            "term_a_file": occ_a.get("file_path", "unknown"),
                            "term_a_heading": occ_a.get("heading", ""),
                            "term_a_context": occ_a.get("snippet", term_a),
                            "term_b": term_b,
                            "term_b_file": occ_b.get("file_path", "unknown"),
                            "term_b_heading": occ_b.get("heading", ""),
                            "term_b_context": occ_b.get("snippet", term_b),
                        },
                        questions={
                            "term_decision": {
                                "type": "choice",
                                "instructions": (
                                    "Compare `term_a` and `term_b` in their provided contexts. "
                                    "Are they inconsistent labels for the same concept, and if so, "
                                    "which existing term should be preferred?"
                                ),
                                "criteria": {
                                    "no_variance": (
                                        "The terms do not refer to the same concept, or their "
                                        "difference is intentional and appropriate."
                                    ),
                                    "variance_prefer_a": (
                                        f"The terms are an undesirable naming variance for the "
                                        f"same concept; prefer '{term_a}'."
                                    ),
                                    "variance_prefer_b": (
                                        f"The terms are an undesirable naming variance for the "
                                        f"same concept; prefer '{term_b}'."
                                    ),
                                },
                            }
                        },
                    )
                    response = submit_checksheet(self.config, sheet, model, used_backend)
                    answer = response["answers"]["term_decision"]
                    decision = answer.get("choice")
                    if decision not in (
                        "no_variance",
                        "variance_prefer_a",
                        "variance_prefer_b",
                    ):
                        raise ValueError(
                            f"{backend_label} returned an unknown term decision: {decision!r}"
                        )
                    is_variance = decision != "no_variance"
                    confidence = float(answer["confidence"])
                    if not 0.0 <= confidence <= 1.0:
                        raise ValueError(
                            f"{backend_label} returned {metric_label} outside the 0-1 range"
                        )
                    preferred_term = term_b if decision == "variance_prefer_b" else term_a
                    reason = (
                        f"{backend_label} selected '{decision}' with {confidence:.0%} "
                        f"{metric_label}; "
                        "the decision model does not return a text rationale."
                    )
                else:
                    is_variance = True
                    confidence = 0.95
                    preferred_term = term_a
                    reason = "Mock term variance judgment"

                db.insert_term_variance_judgment(
                    term_a=term_a,
                    term_b=term_b,
                    file_a=occ_a.get("file_path", ""),
                    file_b=occ_b.get("file_path", ""),
                    line_a=occ_a.get("line_start", 1),
                    line_b=occ_b.get("line_start", 1),
                    is_variance=is_variance,
                    confidence=confidence,
                    preferred_term=preferred_term,
                    reason=reason,
                    backend=used_backend,
                )
                judged_count += 1
            except Exception as e:
                print(f"[Warning] Failed to judge term variance for ('{term_a}', '{term_b}'): {e}")

        db.commit()
        return judged_count

    def generate_verification_issues(
        self,
        db: DocAuditDB,
        min_confidence: float | None = None,
        backend: str | None = None,
    ) -> list[VerificationIssue]:
        """Generates warnings for term-variance scores from the selected backend."""
        threshold = (
            min_confidence
            if min_confidence is not None
            else self.config.terminology.confidence_threshold
        )

        selected_backend = backend or self.config.llm_judge.default_backend
        rows = db.get_high_confidence_variances(min_confidence=threshold, backend=selected_backend)
        issues: list[VerificationIssue] = []

        for r in rows:
            conf_pct = int(r["confidence"] * 100)
            backend_label = BACKEND_LABELS.get(selected_backend, selected_backend)
            metric_label = "確率集中度" if selected_backend == "clef-flash" else "確度"
            msg = (
                f"用語表記揺れの可能性 ({backend_label} {metric_label}: {conf_pct}%): "
                f"'{r['term_a']}' vs '{r['term_b']}' "
                f"({r['file_b']}:{r['line_b']})。推奨表記: '{r['preferred_term']}'。理由: {r['reason']}"
            )
            issues.append(
                VerificationIssue(
                    gate="Consistency",
                    severity="WARNING",
                    file_path=r["file_a"],
                    line=r["line_a"],
                    rule_code="TERM_VARIANCE",
                    message=msg,
                )
            )

        return issues
