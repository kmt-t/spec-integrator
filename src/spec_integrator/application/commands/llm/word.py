from __future__ import annotations

import sys

from spec_integrator.application.commands.common import _load_and_parse_all
from spec_integrator.application.utils import _log
from spec_integrator.checks import LevenshteinTypoCheck
from spec_integrator.config import Config
from spec_integrator.gates.base import GateContext
from spec_integrator.judge.llm_backend import BACKEND_LABELS
from spec_integrator.terminology import (
    TermIndexer,
    TermVarianceJudge,
)


def cmd_llm_word(args):
    """Executes terminology embedding, pairwise similarity indexing, LLM variance judgment, and report."""
    config = Config.load(args.config)
    used_backend = args.backend or config.llm_judge.default_backend
    documents, _graph, db, _docs_root = _load_and_parse_all(config)

    indexer = TermIndexer(config)
    _log(">>> [1/3] Generating term embeddings via Ollama...")
    new_embeddings = indexer.index_embeddings(db, model=args.embedding_model)
    _log(f"✔ Indexed {new_embeddings} new term embedding(s).")

    _log(">>> [2/3] Calculating pairwise similarities for terminology...")
    sim_pairs = indexer.compute_and_save_similarities(
        db, model=args.embedding_model, min_similarity=args.threshold
    )
    _log(f"✔ Identified {sim_pairs} high-similarity term pair(s).")

    if not args.quick:
        judge = TermVarianceJudge(config)
        _log(
            f">>> [3/3] Judging term variance via LLM (backend: {used_backend}, max: {args.max_pairs} pairs)..."
        )
        judged_count = judge.judge_similar_pairs(
            db, backend=args.backend, model=args.model, max_pairs=args.max_pairs
        )
        _log(f"✔ Judged {judged_count} term pair(s) for undesirable variance.")
    else:
        _log(">>> [3/3] Skipping LLM variance judgment (--quick specified).")

    print("\n" + "=" * 80)
    print(" Fireball Terminology & Spelling Variance Report")
    print("=" * 80)

    # 1. Levenshtein static typos (Format Gate)
    ctx = GateContext(
        documents=documents,
        graph=_graph,
        docs_root=_docs_root,
        config=config,
        db=db,
    )
    lev_issues = LevenshteinTypoCheck().check(ctx)
    print(
        f"\n### 1. Static Levenshtein Typos & Variances (Format Gate: {len(lev_issues)} detected)"
    )
    if lev_issues:
        print("-" * 80)
        for issue in lev_issues:
            print(f"  [WARN] {issue.file_path}:{issue.line} - {issue.message}")
    else:
        print("  ✔ No Levenshtein typos found.")

    # 2. LLM Semantic Variance Judgments
    variances = db.get_high_confidence_variances(
        min_confidence=config.terminology.confidence_threshold,
        backend=used_backend,
    )
    conf_thresh = int(config.terminology.confidence_threshold * 100)
    backend_label = BACKEND_LABELS.get(used_backend, used_backend)
    metric_label = "probability concentration" if used_backend == "clef-flash" else "confidence"
    print(
        f"\n### 2. LLM Contextual Term Variances ({backend_label} {metric_label} >= "
        f"{conf_thresh}%: {len(variances)} detected)"
    )
    if variances:
        print("-" * 80)
        for r in variances:
            conf_pct = int(r["confidence"] * 100)
            pref = r["preferred_term"] or "N/A"
            print(f"  [WARN] '{r['term_a']}' vs '{r['term_b']}' ({metric_label}: {conf_pct}%)")
            print(f"         Location: {r['file_a']}:{r['line_a']} vs {r['file_b']}:{r['line_b']}")
            print(f"         Preferred: '{pref}'")
            print(f"         Reason: {r['reason']}\n")
    else:
        print("  ✔ No high-confidence contextual term variances recorded in DB.")

    print("=" * 80)
    total = len(lev_issues) + len(variances)
    print(
        f" Total Terminology Warnings: {total} (Static: {len(lev_issues)}, LLM: {len(variances)})"
    )
    print("=" * 80 + "\n")
    db.close()
    sys.exit(0)
