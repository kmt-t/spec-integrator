from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageContext
from spec_integrator.anti_sabotage.checks import LevenshteinTypoCheck
from spec_integrator.config import Config
from spec_integrator.judge import (
    RiskAssessor,
    UnifiedReviewEngine,
)
from spec_integrator.terminology import (
    TermIndexer,
    TermVarianceJudge,
)
from spec_integrator.document import DocumentFacade
from spec_integrator.document.commands import cmd_build, cmd_check_doc, cmd_format_doc
from spec_integrator.source.commands import cmd_check_src, cmd_format_src


def _configure_utf8_stdio() -> None:
    """Ensure UTF-8 output on Windows consoles."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def _rel_path(p: Path | str) -> str:
    path_obj = Path(p).resolve()
    try:
        return str(path_obj.relative_to(Path.cwd()))
    except ValueError:
        return str(path_obj)


def _write_text(path: str, content: str) -> Path:
    out_p = Path(path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(content, encoding="utf-8")
    return out_p


def _load_and_parse_all(
    config: Config, clean: bool = False, file_paths: list[str | Path] | None = None
):
    document = DocumentFacade(config)
    workspace = document.load_workspace(
        clean=clean,
        file_paths=file_paths,
    )
    _log(f"Parsed {len(workspace.documents)} document(s) in {_rel_path(workspace.docs_root)}.")
    _log(
        f"DocGraph built: {len(workspace.graph.nodes)} nodes, "
        f"{len(workspace.graph.edges)} edges."
    )
    return workspace.documents, workspace.graph, workspace.db, workspace.docs_root


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------
def cmd_init(args):
    target = Path("spec-integrator.yaml")
    if target.exists():
        print(f"[Error] '{target}' already exists.")
        sys.exit(1)

    template = r"""version: "1.0"

project:
  name: "My Specification Project"
  docs_root: "docs"
  cache_db: ".spec-integrator/doc_cache.db"

tiers:
  - tier: 0
    name: "Requirements"
    path_pattern: 'requires/.*\.md'
    description: "System Requirements"

  - tier: 1
    name: "Core"
    path_pattern: 'components/tier1_.*\.md'
    description: "Core System Components"

  - tier: 2
    name: "Runtime"
    path_pattern: 'components/tier2_.*\.md'
    description: "Runtime & Execution Engine"

  - tier: 3
    name: "Platform"
    path_pattern: 'components/tier3_.*\.md'
    description: "Platform Abstraction & Drivers"

  - tier: "meta"
    name: "Architecture & Plans"
    path_pattern: '(architecture|plans)/.*\.md'
    description: "Architecture & Plans"

keywords:
  meta:
    pattern: '^META_[A-Za-z0-9_]+$'
    defined_in: 'architecture/document_structure\.md'
  global:
    pattern: '^GLOBAL_[A-Za-z0-9_]+$'
    defined_in: 'architecture/document_structure\.md'
  local:
    # Allows hyphens: hyphenated per-component GOTCHA-ID keywords
    # (SCHED-GOTCHA-01, DBG-GOTCHA-01, ...) are a common convention and must
    # be classifiable as local keywords to ever resolve as "defined".
    pattern: '^[A-Za-z0-9_-]+$'
    defined_in: 'requires/.*\.md'

formal_verification:
  model_dir_name: "formal"
  tag: "{VERIFY_FORMAL}"
  timeout_seconds: 30
  require_contract: true
  check_vacuity: true
  check_reachability: true
  check_nondeterminism: true
  min_states: 4

llm_judge:
  tag: "{VERIFY_LLM}"
  default_backend: "jev"
  backends:
    jev:
      api_key_env: "OPENROUTER_API_KEY"
      endpoint: "https://openrouter.ai/api/alpha/decisions"
      model: "typesafe/jev-1.13"
    sakura:
      api_key_env: "SAKURA_API_KEY"
      model: "sakura-ai-model"
    ollama:
      endpoint: "http://localhost:11434"
      model: "llama3"

terminology:
  embedding_backend: "openrouter"
  embedding_model: "intfloat/multilingual-e5-large"

evidence:
  enabled: true
  metric_severity: "WARNING"
  ignore_artifact_refs: []

obligation:
  enabled: true
  require_assessment: true
  require_judge: true
  risk_threshold: 4
  stale_is_error: true
"""
    target.write_text(template, encoding="utf-8")
    print(f"✔ Created '{target}'.")
    sys.exit(0)


def cmd_graph(args):
    config = Config.load(args.config)
    _documents, graph, db, _docs_root = _load_and_parse_all(config)
    fmt = args.format.lower()
    if fmt == "json":
        content = json.dumps(graph.to_dict(), indent=2, ensure_ascii=False)
    else:
        content = graph.to_mermaid()

    if args.out:
        out_p = _write_text(args.out, content)
        print(f"✔ Graph saved to {_rel_path(out_p)}")
    else:
        print(content)

    db.close()
    sys.exit(0)


def cmd_risk(args):
    """Evaluates requirement/design keywords complexity and design risk via LLM."""
    config = Config.load(args.config)
    documents, graph, db, _docs_root = _load_and_parse_all(config)
    subgraphs = graph.extract_item_subgraphs()
    assessor = RiskAssessor(config)
    used_backend = args.backend or config.llm_judge.default_backend
    print(f"Running Content Complexity & Risk Assessment (backend: {used_backend})...")
    report = assessor.assess_subgraphs(
        subgraphs,
        documents,
        backend=args.backend,
        model=args.model,
        max_keywords=args.max_keywords,
        exhaustive=args.exhaustive,
        min_references=args.min_references,
    )

    db.replace_risk_assessments([asdict(a) for a in report.assessments], used_backend)
    db.set_assessed_doc_hashes("risk_assessment", {d.file_path: d.content_hash for d in documents})
    db.commit()
    db.close()

    print(f"\nAssessment finished. Evaluated {report.total_evaluated} keyword(s).")
    print(f"  - High risk (>= {config.obligation.risk_threshold}/5): {report.high_risk_count}")
    print("Scores recorded in the cache DB; see 'check' report § Risk Assessment Detail.")
    sys.exit(0)


def cmd_llm_single_review(args):
    """Reviews documents section-by-section and their high-risk keyword islands."""
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
    backend = args.backend or config.llm_judge.default_backend
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
    islands = graph.extract_document_islands(min_size=1)

    has_failures = False
    for doc in target_docs:
        print("\n" + "=" * 80)
        print(f" Auditing Document: '{doc.file_path}' (backend: {backend})")
        print("=" * 80)

        # 1. Section-by-section review
        print(f"\n>>> [1/2] Reviewing sections of '{doc.file_path}'...", flush=True)
        res_single = reviewer.review_single_document(
            doc, backend=backend, model=model, check_ids=selected_checks, dry_run=args.dry_run
        )
        print(f"Result: {res_single.status} - {res_single.summary}")
        if res_single.issues:
            for iss in res_single.issues:
                cid = iss.get("check_id", "CHECK")
                print(
                    f"  [{iss.get('severity', 'WARNING')}] [{cid}] {iss.get('location', '')}: {iss.get('description', '')}"
                )
        if res_single.status == "FAIL":
            has_failures = True

        # 2. Island review for related high-risk keywords
        doc_kws = set(doc.all_keywords)
        high_risk_kws = [kw for kw in doc_kws if risk_records.get(kw, 0) >= high_risk_threshold]

        related_islands = []
        for isl in islands:
            if (
                doc.file_path in isl.file_paths
                and isl.total_docs >= 2
                and set(isl.keywords).intersection(high_risk_kws)
            ):
                if isl not in related_islands:
                    related_islands.append(isl)

        if related_islands:
            print(
                f"\n>>> [2/2] Reviewing {len(related_islands)} high-risk keyword island(s) related to '{doc.file_path}'...",
                flush=True,
            )
            for idx, isl in enumerate(related_islands, start=1):
                print(
                    f"  [{idx}/{len(related_islands)}] Auditing keyword island '{isl.name}' ({isl.total_docs} docs, {isl.total_sections} linked sections)...",
                    flush=True,
                )
                res_isl = reviewer.review_document_island(
                    isl,
                    documents,
                    backend=backend,
                    model=model,
                    check_ids=selected_checks,
                    dry_run=args.dry_run,
                )
                print(f"       -> Status: {res_isl.status} ({res_isl.summary[:70]})")
                if res_isl.issues:
                    for iss in res_isl.issues:
                        cid = iss.get("check_id", "CHECK")
                        print(
                            f"          [{iss.get('severity', 'WARNING')}] [{cid}] {iss.get('location', '')}: {iss.get('description', '')}"
                        )
                if res_isl.status == "FAIL":
                    has_failures = True
        else:
            print("\n>>> [2/2] No connected multi-document islands found for this document.")

    db.close()
    sys.exit(1 if has_failures else 0)


def cmd_llm_keyword_review(args):
    """Reviews per-keyword islands containing high-risk keywords."""
    config = Config.load(args.config)
    reviewer = UnifiedReviewEngine(config)

    if args.list_checks:
        all_checks = reviewer.get_effective_checks("cluster", include_disabled=True)
        print("=" * 80)
        print(" Available LLM Keyword Island Review Checks")
        print("=" * 80)
        for c in all_checks:
            status = "ENABLED " if c.enabled else "DISABLED"
            print(f"  [{status}] {c.id:<26} ({c.severity:<7}) - {c.name}")
        sys.exit(0)

    documents, graph, db, _docs_root = _load_and_parse_all(config)
    backend = args.backend or config.llm_judge.default_backend
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

    islands = graph.extract_document_islands(min_size=2)
    target_keyword_set = set(target_keywords)
    target_islands = [isl for isl in islands if target_keyword_set.intersection(isl.keywords)]
    print(
        f"Found {len(target_islands)} keyword island(s) for the target keywords."
    )

    has_failures = False
    for idx, isl in enumerate(target_islands, start=1):
        print(
            f"\n[{idx}/{len(target_islands)}] Auditing keyword island '{isl.name}' ({isl.total_docs} docs, {isl.total_sections} linked sections)...",
            flush=True,
        )
        res = reviewer.review_document_island(
            isl,
            documents,
            backend=backend,
            model=model,
            check_ids=selected_checks,
            dry_run=args.dry_run,
        )
        print(f"       -> Status: {res.status} ({res.summary[:70]})")
        if res.issues:
            for iss in res.issues:
                cid = iss.get("check_id", "CHECK")
                print(
                    f"          [{iss.get('severity', 'WARNING')}] [{cid}] {iss.get('location', '')}: {iss.get('description', '')}"
                )
        if res.status == "FAIL":
            has_failures = True

    db.close()
    sys.exit(1 if has_failures else 0)


def cmd_llm_judge(args):
    """Runs the anchored LLM semantic judge across all {VERIFY_LLM}-tagged documents.

    Persists results into `document_judge_results` (per-section self-consistency,
    checked by DocumentJudgeCoverageCheck) and `judge_results` (cross-document island
    review, checked by JudgeCoverageCheck), anchored to the current document hashes so
    the Obligation Verifier can detect staleness. This is the command that discharges
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
        print("-- Cluster Island Mode (-> judge_results) --")
        for c in reviewer.get_effective_checks("cluster", include_disabled=True):
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
        db.set_assessed_doc_hashes(
            "document_judge", {d.file_path: d.content_hash for d in doc_targets}
        )

    # Phase 2: cross-document island review -> judge_results
    islands = graph.extract_document_islands(min_size=1)
    related_islands = [isl for isl in islands if tagged_paths & set(isl.file_paths)]
    island_targets = (
        related_islands
        if (args.exhaustive or args.max_subgraphs <= 0)
        else related_islands[: args.max_subgraphs]
    )
    if len(island_targets) < len(related_islands):
        print(
            f"[Warning] {len(related_islands)} island(s) touch tagged documents but "
            f"--max-subgraphs={args.max_subgraphs}; only auditing {len(island_targets)}. "
            "Raise --max-subgraphs or pass -a/--exhaustive for full coverage."
        )
    print(
        f"\n>>> [2/2] Cross-document island review "
        f"({len(island_targets)} island(s), backend: {backend})..."
    )
    island_results = []
    covered_hashes: dict[str, str] = {}
    doc_by_path = {d.file_path: d for d in documents}
    for idx, isl in enumerate(island_targets, start=1):
        print(
            f"  [{idx}/{len(island_targets)}] Auditing island '{isl.name}' "
            f"({isl.total_docs} doc(s))...",
            flush=True,
        )
        res = reviewer.review_document_island(
            isl,
            documents,
            backend=backend,
            model=model,
            check_ids=selected_checks,
            dry_run=args.dry_run,
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
        island_results.append(res)
        for fp in isl.file_paths:
            d = doc_by_path.get(fp)
            if d:
                covered_hashes[fp] = d.content_hash

    if not args.dry_run:
        db.replace_judge_results(island_results, backend)
        db.set_assessed_doc_hashes("judge", covered_hashes)
        db.commit()

    db.close()
    sys.exit(1 if has_failures else 0)


def cmd_llm_word(args):
    """Executes terminology embedding, pairwise similarity indexing, LLM variance judgment, and report."""
    config = Config.load(args.config)
    documents, _graph, db, _docs_root = _load_and_parse_all(config)

    indexer = TermIndexer(config)
    embedding_backend = config.terminology.embedding_backend
    _log(f">>> [1/3] Generating term embeddings via {embedding_backend}...")
    new_embeddings = indexer.index_embeddings(db, model=args.embedding_model)
    _log(f"✔ Indexed {new_embeddings} new term embedding(s).")

    _log(">>> [2/3] Calculating pairwise similarities for terminology...")
    sim_pairs = indexer.compute_and_save_similarities(
        db, model=args.embedding_model, min_similarity=args.threshold
    )
    _log(f"✔ Identified {sim_pairs} high-similarity term pair(s).")

    if not args.quick:
        judge = TermVarianceJudge(config)
        used_backend = args.backend or config.llm_judge.default_backend
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
    ctx = AntiSabotageContext(
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
        min_confidence=config.terminology.confidence_threshold
    )
    conf_thresh = int(config.terminology.confidence_threshold * 100)
    print(
        f"\n### 2. LLM Contextual Term Variances (Confidence >= {conf_thresh}%: {len(variances)} detected)"
    )
    if variances:
        print("-" * 80)
        for r in variances:
            conf_pct = int(r["confidence"] * 100)
            pref = r["preferred_term"] or "N/A"
            print(f"  [WARN] '{r['term_a']}' vs '{r['term_b']}' (Confidence: {conf_pct}%)")
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


# ---------------------------------------------------------------------------
# CLI Argument Parsers
# ---------------------------------------------------------------------------
def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c", "--config", default="spec-integrator.yaml", help="Path to configuration file"
    )


def _add_init_subparser(subparsers) -> None:
    p = subparsers.add_parser("init", help="Initialize spec-integrator.yaml configuration")
    p.set_defaults(func=cmd_init)


def _add_build_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "build", help="Build document database and TF-IDF keyword/terminology index"
    )
    _add_config_arg(p)
    p.add_argument("--clean", action="store_true", help="Clear cache DB and rebuild cleanly")
    p.add_argument("files", nargs="*", help="Optional list of markdown documents to index")
    p.set_defaults(func=cmd_build)


def _add_format_doc_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "format-doc", help="Format markdown documents (trailing whitespace, newlines)"
    )
    _add_config_arg(p)
    p.add_argument("files", nargs="*", help="Optional list of markdown documents to format")
    p.set_defaults(func=cmd_format_doc)


def _add_check_doc_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "check-doc", help="Run static document verification & 8 quality gates"
    )
    _add_config_arg(p)
    p.add_argument("-r", "--report", default="reports/doc_report.md", help="Markdown report output path")
    p.add_argument("--clean", action="store_true", help="Clear cache DB and run clean audit")
    p.add_argument("files", nargs="*", help="Optional list of markdown documents to verify")
    p.set_defaults(func=cmd_check_doc)


def _add_format_src_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "format-src", help="Format source code (Ruff for Python, clang-format for C++)"
    )
    _add_config_arg(p)
    p.add_argument(
        "-g", "--group", help="Source group to format (cpp, python, concepts, formal, pysim, all)"
    )
    p.add_argument("files", nargs="*", help="Optional list of source files to format")
    p.set_defaults(func=cmd_format_src)


def _add_check_src_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "check-src", help="Verify source code (anti-sabotage, language rules, test execution)"
    )
    _add_config_arg(p)
    p.add_argument(
        "-g", "--group", help="Source group to verify (cpp, python, concepts, formal, pysim, all)"
    )
    p.add_argument("files", nargs="*", help="Optional list of source files to verify")
    p.set_defaults(func=cmd_check_src)


def _add_graph_subparser(subparsers) -> None:
    p = subparsers.add_parser("graph", help="Extract and visualize DocGraph")
    _add_config_arg(p)
    p.add_argument(
        "-f", "--format", choices=["mermaid", "json"], default="mermaid", help="Output format"
    )
    p.add_argument("-o", "--out", help="Output file path")
    p.set_defaults(func=cmd_graph)


def _add_risk_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "risk",
        help="Score requirement/design keywords complexity and design risk via LLM",
    )
    _add_config_arg(p)
    p.add_argument(
        "--backend",
        choices=["jev", "openrouter", "sakura", "ollama", "mock"],
        help="Risk assessor backend",
    )
    p.add_argument("--model", help="LLM model name override")
    p.add_argument(
        "--max-keywords",
        type=int,
        default=15,
        help="Max keywords to assess (0 for unlimited).",
    )
    p.add_argument(
        "-a",
        "--exhaustive",
        action="store_true",
        help="Exhaustive assessment across all keywords.",
    )
    p.add_argument(
        "--min-references",
        type=int,
        default=0,
        help="Minimum referencing sections required to include a keyword (default: 0).",
    )
    p.set_defaults(func=cmd_risk)


def _add_llm_word_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-word",
        help="Index embeddings, link similar terms, judge variance via LLM, and output report",
    )
    _add_config_arg(p)
    p.add_argument(
        "--backend",
        choices=["jev", "openrouter", "sakura", "ollama", "mock"],
        help="LLM backend",
    )
    p.add_argument("--model", help="LLM model name override")
    p.add_argument("--embedding-model", help="Embedding model name override")
    p.add_argument(
        "--threshold",
        type=float,
        default=0.80,
        help="Cosine similarity threshold for term linking (default: 0.80)",
    )
    p.add_argument(
        "--max-pairs",
        type=int,
        default=20,
        help="Max candidate pairs to judge (0 for unlimited).",
    )
    p.add_argument(
        "--quick",
        action="store_true",
        help="Skip LLM variance judgment; missing embeddings may still call the configured API",
    )
    p.set_defaults(func=cmd_llm_word)


def _add_llm_single_review_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-single-review",
        help="LLM review for single documents (section-by-section) and related high-risk keyword islands",
    )
    _add_config_arg(p)
    p.add_argument(
        "-f",
        "--file",
        help="Path to markdown document to review",
    )
    p.add_argument(
        "-t",
        "--tagged",
        action="store_true",
        help="Review only documents tagged with {VERIFY_LLM}",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Review all documents in the project",
    )
    p.add_argument(
        "--risk-threshold",
        type=int,
        help="Override high risk threshold for keyword islands",
    )
    p.add_argument(
        "--check",
        help="Run only a specific check ID",
    )
    p.add_argument(
        "--list-checks",
        action="store_true",
        help="List all configured review checks and exit",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and display prompt without calling LLM backend",
    )
    p.add_argument(
        "--backend",
        choices=["jev", "openrouter", "sakura", "ollama", "mock"],
        help="LLM backend override",
    )
    p.add_argument("--model", help="LLM model name override")
    p.set_defaults(func=cmd_llm_single_review)


def _add_llm_keyword_review_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-keyword-review",
        help="LLM review for same-keyword section islands containing high-risk keywords",
    )
    _add_config_arg(p)
    p.add_argument(
        "--keyword",
        help="Specific high-risk keyword to target",
    )
    p.add_argument(
        "--min-risk",
        type=int,
        help="Minimum risk score to filter keywords (default from config)",
    )
    p.add_argument(
        "--check",
        help="Run only a specific check ID",
    )
    p.add_argument(
        "--list-checks",
        action="store_true",
        help="List all configured review checks and exit",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and display prompt without calling LLM backend",
    )
    p.add_argument(
        "--backend",
        choices=["jev", "openrouter", "sakura", "ollama", "mock"],
        help="LLM backend override",
    )
    p.add_argument("--model", help="LLM model name override")
    p.set_defaults(func=cmd_llm_keyword_review)


def _add_llm_judge_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-judge",
        help=(
            "Anchored LLM semantic judge for all {VERIFY_LLM}-tagged documents "
            "(discharges the OBLIG-JUDGE-* / OBLIG-DOC-JUDGE-* obligations)"
        ),
    )
    _add_config_arg(p)
    p.add_argument(
        "--max-documents",
        type=int,
        default=20,
        help="Max tagged documents to audit in per-section mode (default: 20, 0 for unlimited).",
    )
    p.add_argument(
        "--max-subgraphs",
        type=int,
        default=20,
        help="Max document islands to audit in cluster mode (default: 20, 0 for unlimited).",
    )
    p.add_argument(
        "-a",
        "--exhaustive",
        action="store_true",
        help="Ignore --max-documents/--max-subgraphs and audit full coverage.",
    )
    p.add_argument(
        "--check",
        help="Run only a specific check ID",
    )
    p.add_argument(
        "--list-checks",
        action="store_true",
        help="List all configured single/cluster review checks and exit",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and display prompts without calling the LLM backend or persisting results",
    )
    p.add_argument(
        "--backend",
        choices=["jev", "openrouter", "sakura", "ollama", "mock"],
        help="LLM backend override",
    )
    p.add_argument("--model", help="LLM model name override")
    p.set_defaults(func=cmd_llm_judge)


_SUBPARSER_BUILDERS = (
    _add_init_subparser,
    _add_build_subparser,
    _add_format_doc_subparser,
    _add_check_doc_subparser,
    _add_format_src_subparser,
    _add_check_src_subparser,
    _add_graph_subparser,
    _add_risk_subparser,
    _add_llm_word_subparser,
    _add_llm_single_review_subparser,
    _add_llm_keyword_review_subparser,
    _add_llm_judge_subparser,
)


def main():
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="spec-integrator",
        description="Universal Document Quality, Traceability, Formal Verification & LLM Judge Tool",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")
    for build_subparser in _SUBPARSER_BUILDERS:
        build_subparser(subparsers)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
