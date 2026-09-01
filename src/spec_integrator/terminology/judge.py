from __future__ import annotations

import json
from typing import TYPE_CHECKING

from spec_integrator.db import VerificationIssue
from spec_integrator.judge.llm_backend import (
    call_ollama,
    call_openrouter,
    call_sakura,
    extract_json,
)

if TYPE_CHECKING:
    from spec_integrator.config import Config
    from spec_integrator.db import DocAuditDB

TERM_JUDGE_PROMPT = """\
You are an expert auditor for technical documentation consistency and terminology standardization.
Compare the following two terms and the context snippets where they appear in the specification documents.

[Term A]: {term_a}
Location: {file_a}:{line_a} (Section: {heading_a})
Context Snippet:
{snippet_a}

[Term B]: {term_b}
Location: {file_b}:{line_b} (Section: {heading_b})
Context Snippet:
{snippet_b}

[Audit Instructions]
Taking the context into account, determine whether these two terms represent "undesirable term variance" (i.e. inconsistent spelling or uncoordinated synonyms referring to the exact same concept/entity that should be unified), or if they refer to distinct, legitimate technical concepts.
- If they refer to distinct technical concepts, separate components, or deliberately different abstractions (e.g. "interrupt handler" vs "interrupt vector", or "handler" vs "handle"), it is NOT a term variance (set "is_variance": false).
- If they refer to the identical concept/entity but differ merely in phonetic elongation (chōonpu, e.g. "ハイパーバイザ" vs "ハイパーバイザー"), okurigana variations (e.g. "割込" vs "割り込み", "切替" vs "切り替え"), or uncoordinated synonyms for the same component, it IS an undesirable term variance (set "is_variance": true).
- Specify "confidence" as a float between 0.0 (definitely not variance) and 1.0 (certainly undesirable variance).
- In "preferred_term", suggest which term should be the standardized canonical term.
- In "reason", provide a concise explanation (in Japanese) of your determination.

Respond strictly in the following JSON format:
```json
{{
  "is_variance": true,
  "confidence": 0.95,
  "preferred_term": "<preferred canonical term>",
  "reason": "<explanation in Japanese>"
}}
```
"""


class TermVarianceJudge:
    """Uses an LLM to evaluate whether semantically similar terms represent undesirable term variance."""

    def __init__(self, config: Config):
        self.config = config

    def _call_backend(self, prompt: str, backend: str, model: str | None) -> str:
        b = backend.lower()
        if b == "sakura":
            return call_sakura(self.config, prompt, model)
        if b == "openrouter":
            return call_openrouter(self.config, prompt, model)
        if b == "ollama":
            return call_ollama(self.config, prompt, model)
        if b == "mock":
            return json.dumps(
                {
                    "is_variance": True,
                    "confidence": 0.95,
                    "preferred_term": "モック統一表記",
                    "reason": "Mock term variance judgment",
                }
            )
        raise ValueError(f"Unknown LLM backend: '{backend}'")

    def judge_similar_pairs(
        self,
        db: DocAuditDB,
        backend: str | None = None,
        model: str | None = None,
        max_pairs: int = 20,
    ) -> int:
        """Evaluates high-similarity pairs with LLM context check and records variance judgments."""
        used_backend = backend or self.config.llm_judge.default_backend
        similarities = db.get_term_similarities()

        unjudged_pairs = [
            row for row in similarities if not db.is_similarity_judged(row["term_a"], row["term_b"])
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

            prompt = TERM_JUDGE_PROMPT.format(
                term_a=term_a,
                file_a=occ_a.get("file_path", "unknown"),
                line_a=occ_a.get("line_start", 1),
                heading_a=occ_a.get("heading", ""),
                snippet_a=occ_a.get("snippet", term_a),
                term_b=term_b,
                file_b=occ_b.get("file_path", "unknown"),
                line_b=occ_b.get("line_start", 1),
                heading_b=occ_b.get("heading", ""),
                snippet_b=occ_b.get("snippet", term_b),
            )

            try:
                raw = self._call_backend(prompt, used_backend, model)
                data = extract_json(raw)
                is_var = bool(data.get("is_variance", False))
                conf = float(data.get("confidence", 0.0))
                pref = str(data.get("preferred_term", term_a))
                reason = str(data.get("reason", ""))

                db.insert_term_variance_judgment(
                    term_a=term_a,
                    term_b=term_b,
                    file_a=occ_a.get("file_path", ""),
                    file_b=occ_b.get("file_path", ""),
                    line_a=occ_a.get("line_start", 1),
                    line_b=occ_b.get("line_start", 1),
                    is_variance=is_var,
                    confidence=conf,
                    preferred_term=pref,
                    reason=reason,
                    backend=used_backend,
                )
                judged_count += 1
            except Exception as e:
                print(f"[Warning] Failed to judge term variance for ('{term_a}', '{term_b}'): {e}")

        db.commit()
        return judged_count

    def generate_verification_issues(
        self, db: DocAuditDB, min_confidence: float | None = None
    ) -> list[VerificationIssue]:
        """Generates VerificationIssue warnings for high-confidence term variances."""
        threshold = (
            min_confidence
            if min_confidence is not None
            else getattr(self.config.terminology, "confidence_threshold", 0.70)
        )

        rows = db.get_high_confidence_variances(min_confidence=threshold)
        issues: list[VerificationIssue] = []

        for r in rows:
            conf_pct = int(r["confidence"] * 100)
            msg = (
                f"用語表記揺れの可能性 (確度: {conf_pct}%): '{r['term_a']}' vs '{r['term_b']}' "
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
