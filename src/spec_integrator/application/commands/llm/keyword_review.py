from __future__ import annotations

import sys

from spec_integrator.application.commands.common import _load_and_parse_all
from spec_integrator.config import Config
from spec_integrator.judge import (
    UnifiedReviewEngine,
)
from spec_integrator.models import JudgeResult


def cmd_llm_keyword_review(args):
    """Reviews high-risk keyword definition/reference section pairs."""
    config = Config.load(args.config)
    reviewer = UnifiedReviewEngine(config)

    if args.list_checks:
        all_checks = reviewer.get_effective_checks("link_pair", include_disabled=True)
        print("=" * 80)
        print(" Available LLM Keyword Link-Pair Review Checks")
        print("=" * 80)
        for c in all_checks:
            status = "ENABLED " if c.enabled else "DISABLED"
            print(f"  [{status}] {c.id:<26} ({c.severity:<7}) - {c.name}")
        sys.exit(0)

    documents, graph, db, _docs_root = _load_and_parse_all(config)
    backend = getattr(args, "backend", None) or config.llm_judge.default_backend
    model = args.model
    selected_checks = [args.check] if args.check else None
    min_risk = args.min_risk if args.min_risk is not None else config.obligation.risk_threshold

    if args.keyword:
        target_keywords = [args.keyword]
        print(f"Targeting specified keyword: '{args.keyword}'")
    else:
        risk_records = db.get_risk_assessments()
        target_keywords = [r["keyword"] for r in risk_records if r.get("risk_score", 0) >= min_risk]
        print(
            f"Found {len(target_keywords)} high-risk keyword(s) with risk >= {min_risk} in cache DB."
        )

    if not target_keywords:
        print(
            "No high-risk keywords found to review. Run 'spec-integrator risk' first, or specify '--keyword <KW>'."
        )
        db.close()
        sys.exit(0)

    keyword_groups = graph.extract_keyword_groups(min_size=2)
    target_keyword_set = set(target_keywords)
    target_groups = [group for group in keyword_groups if group.keyword in target_keyword_set]
    print(f"Found {len(target_groups)} keyword group(s) for the target keywords.")

    has_failures = False
    review_results: list[JudgeResult] = []
    for idx, group in enumerate(target_groups, start=1):
        print(
            f"\n[{idx}/{len(target_groups)}] Building definition/reference pairs for '{{{group.keyword}}}'...",
            flush=True,
        )
        pair_results = reviewer.review_keyword_link_pairs(
            group,
            documents,
            backend=backend,
            model=model,
            check_ids=selected_checks,
            dry_run=args.dry_run,
        )
        print(f"       -> Completed {len(pair_results)} link-pair review(s).")
        for pair_result in pair_results:
            review_results.append(pair_result)
            print(
                f"          {pair_result.item_label}: {pair_result.status} "
                f"({pair_result.summary[:70]})"
            )
            if pair_result.issues:
                for iss in pair_result.issues:
                    cid = iss.get("check_id", "CHECK")
                    print(
                        f"             [{iss.get('severity', 'WARNING')}] [{cid}] "
                        f"{iss.get('location', '')}: {iss.get('description', '')}"
                    )
            if pair_result.status == "FAIL":
                has_failures = True

    if not args.dry_run:
        db.save_judge_evaluations(
            "llm-keyword-review",
            review_results,
            backend,
            replace_all=bool(args.keyword is None and args.min_risk is None),
            replace_keywords=(
                None if args.keyword is None and args.min_risk is None else target_keywords
            ),
        )
        db.commit()
    db.close()
    sys.exit(1 if has_failures else 0)
