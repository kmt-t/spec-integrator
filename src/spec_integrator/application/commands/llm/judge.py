from __future__ import annotations

import sys

from spec_integrator.application.commands.common import _load_and_parse_all
from spec_integrator.config import Config
from spec_integrator.judge import (
    UnifiedReviewEngine,
)


def cmd_llm_judge(args):
    """Runs the anchored LLM semantic judge across all {VERIFY_LLM}-tagged documents.

    Persists results into `document_judge_results` (per-section self-consistency,
    checked by DocumentJudgeCoverageCheck) and `judge_results` (cross-document keyword
    link-pair review, checked by JudgeCoverageCheck), anchored to the current document hashes so
    the Obligation gate can detect staleness. This is the command that discharges
    the OBLIG-JUDGE-* / OBLIG-DOC-JUDGE-* obligations.
    """
    config = Config.load(args.config)
    reviewer = UnifiedReviewEngine(config)

    if args.list_checks:
        print("=" * 80)
        print(" Available LLM Judge Checks")
        print("=" * 80)
        print("-- Single Document Mode (-> document_judge_results) --")
        for c in reviewer.get_effective_checks("single", include_disabled=True):
            status = "ENABLED " if c.enabled else "DISABLED"
            print(f"  [{status}] {c.id:<26} ({c.severity:<7}) - {c.name}")
        print("-- Keyword Link-Pair Mode (-> judge_results) --")
        for c in reviewer.get_effective_checks("link_pair", include_disabled=True):
            status = "ENABLED " if c.enabled else "DISABLED"
            print(f"  [{status}] {c.id:<26} ({c.severity:<7}) - {c.name}")
        sys.exit(0)

    documents, graph, db, _docs_root = _load_and_parse_all(config)
    backend = args.backend or config.llm_judge.default_backend
    model = args.model
    selected_checks = [args.check] if args.check else None

    llm_tag = config.llm_judge.tag
    tagged = [d for d in documents if llm_tag in d.all_tags]
    if not tagged:
        print(f"No documents found tagged with '{llm_tag}'. Nothing to judge.")
        db.close()
        sys.exit(0)
    tagged_paths = {d.file_path for d in tagged}
    print(f"Found {len(tagged)} document(s) tagged with '{llm_tag}'.")

    has_failures = False

    # Phase 1: per-section self-consistency review -> document_judge_results
    doc_targets = (
        tagged if (args.exhaustive or args.max_documents <= 0) else tagged[: args.max_documents]
    )
    if len(doc_targets) < len(tagged):
        print(
            f"[Warning] {len(tagged)} tagged document(s) but --max-documents={args.max_documents}; "
            f"only auditing {len(doc_targets)}. Raise --max-documents or pass -a/--exhaustive "
            "for full coverage."
        )
    print(
        f"\n>>> [1/2] Per-section self-consistency review "
        f"({len(doc_targets)} document(s), backend: {backend})..."
    )
    doc_results = []
    for idx, doc in enumerate(doc_targets, start=1):
        print(f"  [{idx}/{len(doc_targets)}] Auditing '{doc.file_path}'...", flush=True)
        res = reviewer.review_single_document(
            doc, backend=backend, model=model, check_ids=selected_checks, dry_run=args.dry_run
        )
        print(f"       -> Status: {res.status} ({res.summary[:70]})")
        for iss in res.issues:
            cid = iss.get("check_id", "CHECK")
            print(
                f"          [{iss.get('severity', 'WARNING')}] [{cid}] "
                f"{iss.get('location', '')}: {iss.get('description', '')}"
            )
        if res.status == "FAIL":
            has_failures = True
        doc_results.append(res)

    if not args.dry_run:
        db.replace_document_judge_results(doc_results, backend)
        db.save_judge_evaluations("llm-judge-document", doc_results, backend, replace_all=True)
        db.set_assessed_doc_hashes(
            "document_judge", {d.file_path: d.content_hash for d in doc_targets}
        )

    # Phase 2: cross-document definition/reference link-pair review -> judge_results
    keyword_groups = graph.extract_keyword_groups(min_size=1)
    related_groups = [group for group in keyword_groups if tagged_paths & set(group.file_paths)]
    group_targets = (
        related_groups
        if (args.exhaustive or args.max_keyword_groups <= 0)
        else related_groups[: args.max_keyword_groups]
    )
    if len(group_targets) < len(related_groups):
        print(
            f"[Warning] {len(related_groups)} keyword group(s) touch tagged documents but "
            f"--max-keyword-groups={args.max_keyword_groups}; only auditing {len(group_targets)}. "
            "Raise --max-keyword-groups or pass -a/--exhaustive for full coverage."
        )
    print(
        f"\n>>> [2/2] Cross-document keyword link-pair review "
        f"({len(group_targets)} keyword group(s), backend: {backend})..."
    )
    link_pair_results = []
    covered_hashes: dict[str, str] = {}
    doc_by_path = {d.file_path: d for d in documents}
    for idx, group in enumerate(group_targets, start=1):
        print(
            f"  [{idx}/{len(group_targets)}] Building link pairs for '{{{group.keyword}}}' "
            f"({group.total_docs} doc(s), {group.total_sections} linked section(s))...",
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
        print(f"       -> Completed {len(pair_results)} definition/reference link-pair review(s).")
        for pair_result in pair_results:
            print(
                f"          {pair_result.item_label}: {pair_result.status} "
                f"({pair_result.summary[:70]})"
            )
            for iss in pair_result.issues:
                cid = iss.get("check_id", "CHECK")
                print(
                    f"             [{iss.get('severity', 'WARNING')}] [{cid}] "
                    f"{iss.get('location', '')}: {iss.get('description', '')}"
                )
            if pair_result.status == "FAIL":
                has_failures = True
            link_pair_results.append(pair_result)
        for fp in group.file_paths:
            d = doc_by_path.get(fp)
            if d:
                covered_hashes[fp] = d.content_hash

    if not args.dry_run:
        db.replace_judge_results(link_pair_results, backend)
        db.save_judge_evaluations(
            "llm-judge-link-pair", link_pair_results, backend, replace_all=True
        )
        db.set_assessed_doc_hashes("judge", covered_hashes)
        db.commit()

    db.close()
    sys.exit(1 if has_failures else 0)
