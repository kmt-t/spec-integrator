from __future__ import annotations

import sys

from spec_integrator.application.commands.common import _load_and_parse_all
from spec_integrator.config import Config
from spec_integrator.judge import (
    UnifiedReviewEngine,
)
from spec_integrator.models import JudgeResult


def cmd_llm_single_review(args):
    """Reviews documents section-by-section and their high-risk keyword link pairs."""
    config = Config.load(args.config)
    reviewer = UnifiedReviewEngine(config)

    if args.list_checks:
        all_checks = reviewer.get_effective_checks("single", include_disabled=True)
        print("=" * 80)
        print(" Available LLM Single Review Checks")
        print("=" * 80)
        for c in all_checks:
            status = "ENABLED " if c.enabled else "DISABLED"
            print(f"  [{status}] {c.id:<26} ({c.severity:<7}) - {c.name}")
        sys.exit(0)

    documents, graph, db, _docs_root = _load_and_parse_all(config)
    backend = getattr(args, "backend", None) or config.llm_judge.default_backend
    model = args.model
    selected_checks = [args.check] if args.check else None
    high_risk_threshold = getattr(args, "risk_threshold", None) or config.obligation.risk_threshold

    if args.file:
        target_norm = str(args.file).replace("\\", "/").removeprefix("./").removeprefix("docs/")
        target_docs = [
            d for d in documents if d.file_path == target_norm or d.file_path.endswith(target_norm)
        ]
        if not target_docs:
            print(f"[Error] Document not found: {args.file}")
            db.close()
            sys.exit(1)
    elif args.tagged:
        llm_tag = config.llm_judge.tag
        target_docs = [d for d in documents if llm_tag in d.all_tags]
        if not target_docs:
            print(f"No documents found tagged with '{llm_tag}'.")
            db.close()
            sys.exit(0)
        print(f"Targeting {len(target_docs)} document(s) tagged with '{llm_tag}'...")
    elif args.all:
        target_docs = documents
    else:
        print("Please specify a document target: --file <path>, --tagged, or --all.")
        db.close()
        sys.exit(1)

    risk_records = {r["keyword"]: r.get("risk_score", 0) for r in db.get_risk_assessments()}
    keyword_groups = graph.extract_keyword_groups(min_size=1)

    has_failures = False
    document_results: list[JudgeResult] = []
    link_pair_results: dict[str, JudgeResult] = {}
    reviewed_keyword_group_ids: set[str] = set()
    reviewed_keyword_names: set[str] = set()
    for doc in target_docs:
        print("\n" + "=" * 80)
        print(f" Auditing Document: '{doc.file_path}' (backend: {backend})")
        print("=" * 80)

        # 1. Section-by-section review
        print(f"\n>>> [1/2] Reviewing sections of '{doc.file_path}'...", flush=True)
        res_single = reviewer.review_single_document(
            doc, backend=backend, model=model, check_ids=selected_checks, dry_run=args.dry_run
        )
        document_results.append(res_single)
        print(f"Result: {res_single.status} - {res_single.summary}")
        if res_single.issues:
            for iss in res_single.issues:
                cid = iss.get("check_id", "CHECK")
                print(
                    f"  [{iss.get('severity', 'WARNING')}] [{cid}] {iss.get('location', '')}: {iss.get('description', '')}"
                )
        if res_single.status == "FAIL":
            has_failures = True

        # 2. Definition/reference pair review for related high-risk keywords
        doc_kws = set(doc.all_keywords)
        high_risk_kws = [kw for kw in doc_kws if risk_records.get(kw, 0) >= high_risk_threshold]

        related_groups = []
        for group in keyword_groups:
            if (
                doc.file_path in group.file_paths
                and group.total_docs >= 2
                and group.keyword in high_risk_kws
            ):
                if group.group_id not in reviewed_keyword_group_ids:
                    related_groups.append(group)
                    reviewed_keyword_group_ids.add(group.group_id)
                    reviewed_keyword_names.add(group.keyword)

        if related_groups:
            print(
                f"\n>>> [2/2] Reviewing {len(related_groups)} high-risk keyword group(s) related to '{doc.file_path}'...",
                flush=True,
            )
            for idx, group in enumerate(related_groups, start=1):
                print(
                    f"  [{idx}/{len(related_groups)}] Building definition/reference link pairs for '{{{group.keyword}}}'...",
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
                for pair_result in pair_results:
                    link_pair_results[pair_result.item_id] = pair_result
                    print(
                        f"       -> {pair_result.item_label}: {pair_result.status} "
                        f"({pair_result.summary[:70]})"
                    )
                    if pair_result.issues:
                        for iss in pair_result.issues:
                            cid = iss.get("check_id", "CHECK")
                            print(
                                f"          [{iss.get('severity', 'WARNING')}] [{cid}] "
                                f"{iss.get('location', '')}: {iss.get('description', '')}"
                            )
                    if pair_result.status == "FAIL":
                        has_failures = True
        else:
            print("\n>>> [2/2] No connected high-risk keyword groups found for this document.")

    if not args.dry_run:
        db.save_judge_evaluations(
            "llm-single-review",
            document_results,
            backend,
            replace_all=bool(args.all),
            replace_keywords=None if args.all else sorted(reviewed_keyword_names),
        )
        db.save_judge_evaluations(
            "llm-single-review-link-pair",
            list(link_pair_results.values()),
            backend,
            replace_all=bool(args.all),
        )
        db.commit()
    db.close()
    sys.exit(1 if has_failures else 0)
