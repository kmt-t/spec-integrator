from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageContext
from spec_integrator.anti_sabotage.checks import LevenshteinTypoCheck
from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.graph import DocGraphBuilder
from spec_integrator.judge import (
    RiskAssessor,
    UnifiedReviewEngine,
)
from spec_integrator.models import ParsedDocument
from spec_integrator.parser import MarkdownParser
from spec_integrator.reporter import Reporter
from spec_integrator.terminology import (
    TermExtractor,
    TermIndexer,
    TermVarianceJudge,
)
from spec_integrator.verifier import (
    ConsistencyVerifier,
    EvidenceVerifier,
    FormalVerifier,
    ObligationVerifier,
    SectionTopicVerifier,
    StaticVerifier,
    WITVerifier,
)


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
    docs_root = config.get_docs_dir()
    if not docs_root.exists():
        print(f"[Error] Docs directory not found: {docs_root}", flush=True)
        sys.exit(1)

    db_path = config.get_db_path()
    db = DocAuditDB(db_path)
    if clean:
        db.clear_all()

    parser = MarkdownParser(config)
    if file_paths:
        md_files = [
            Path(f).resolve()
            for f in file_paths
            if Path(f).is_file() and not config.is_excluded(f, docs_root)
        ]
    else:
        all_md = sorted(docs_root.rglob("*.md"))
        md_files = [f for f in all_md if not config.is_excluded(f, docs_root)]
    _log(f"Scanning {len(md_files)} markdown files in {_rel_path(docs_root)}...")
    documents: list[ParsedDocument] = []
    for md_file in md_files:
        doc = parser.parse_file(md_file, docs_root)
        documents.append(doc)
        db.insert_document(doc.file_path, doc.tier, doc.component, doc.content_hash)
        for sec in doc.sections:
            sec_hash = parser.config_compute_hash(sec.body_text)
            db.insert_section(
                sec.section_id,
                doc.file_path,
                sec.heading,
                sec.level,
                sec.line_start,
                sec.line_end,
                sec.body_text,
                sec_hash,
            )
            for kw in sec.keywords:
                is_def = (
                    "defines" if config.is_keyword_definition(kw, doc.file_path) else "refers_to"
                )
                db.insert_keyword_reference(
                    kw, doc.file_path, sec.section_id, is_def, sec.line_start
                )

        for link in doc.all_links:
            db.insert_link(
                link.source_file,
                link.source_line,
                link.target_path,
                link.target_anchor,
                1,
            )

    db.commit()
    _log("Building DocGraph topology...")
    graph_builder = DocGraphBuilder(config)
    graph = graph_builder.build(documents, docs_root)
    _log(f"DocGraph built: {len(graph.nodes)} nodes, {len(graph.edges)} edges.")
    return documents, graph, db, docs_root


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
  default_backend: "sakura"
  backends:
    sakura:
      api_key_env: "SAKURA_API_KEY"
      model: "sakura-ai-model"
    ollama:
      endpoint: "http://localhost:11434"
      model: "llama3"

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


def cmd_build(args):
    """Parses documents, builds DocGraph, and generates the TF-IDF keyword/term list."""
    config = Config.load(args.config)
    _log("=" * 80)
    _log(f" Spec-Integrator: Building Document Database [{config.project.name}]")
    _log("=" * 80)
    clean = getattr(args, "clean", False)
    files = getattr(args, "files", None)
    documents, graph, db, docs_root = _load_and_parse_all(
        config, clean=clean, file_paths=files
    )
    _log(f"✔ Parsed {len(documents)} document(s), {len(graph.nodes)} node(s).")

    # TF-IDF Terminology & Keyword Extraction
    _log("Extracting terminology & keywords via TF-IDF...")
    extractor = TermExtractor(config)
    terms_count = extractor.extract_and_save(documents, db)
    _log(f"✔ Extracted and indexed {terms_count} terms via TF-IDF.")

    db.close()
    _log("=" * 80)
    _log(f"✔ Database build complete: {config.get_db_path()}")
    _log("=" * 80)
    sys.exit(0)


def cmd_format_doc(args):
    """Applies static formatting (normalizing trailing spaces, newlines) to markdown documents."""
    config = Config.load(args.config)
    _log("=" * 80)
    _log(" Spec-Integrator: Document Formatter (Markdown)")
    _log("=" * 80)
    docs_root = config.get_docs_dir()
    if getattr(args, "files", None):
        target_files = [Path(f).resolve() for f in args.files if Path(f).is_file()]
    else:
        target_files = [
            f
            for f in sorted(docs_root.rglob("*.md"))
            if not config.is_excluded(f, docs_root)
        ]

    formatted_count = 0
    for f in target_files:
        try:
            content = f.read_text(encoding="utf-8")
            lines = [line.rstrip() for line in content.splitlines()]
            new_content = "\n".join(lines) + "\n" if lines else ""
            if new_content != content:
                f.write_text(new_content, encoding="utf-8")
                formatted_count += 1
        except Exception as e:
            _log(f"  [Warning] Failed to format {f.name}: {e}")

    _log(
        f"✔ Document formatting complete. Normalized {formatted_count}/{len(target_files)} file(s)."
    )
    sys.exit(0)


def cmd_format_src(args):
    """Applies static formatters (Ruff for Python, clang-format for C++) to source code."""
    import shutil
    import subprocess
    from spec_integrator.source_verifier import SourceVerifier

    config = Config.load(args.config)
    _log("=" * 80)
    _log(" Spec-Integrator: Source Formatter")
    _log("=" * 80)

    verifier = SourceVerifier(config)
    group = getattr(args, "group", None)
    group_names = verifier.resolve_group_names(group)
    explicit_files = getattr(args, "files", None)

    total_formatted = 0
    for gname in group_names:
        files = verifier.collect_files_for_group(gname, explicit_files)
        if not files:
            continue

        g_cfg = config.source_verification.groups.get(gname)
        formatters = g_cfg.formatters if g_cfg else ["ruff"]

        for fmt in formatters:
            if fmt == "ruff":
                py_files = [str(f) for f in files if f.suffix.lower() == ".py"]
                if py_files:
                    _log(f"[{gname}] Formatting {len(py_files)} Python file(s) with Ruff...")
                    ruff_bin = shutil.which("ruff")
                    base_cmd = (
                        [ruff_bin]
                        if ruff_bin
                        else ["uv", "run", "--system-certs", "--with", "ruff", "ruff"]
                    )
                    subprocess.run([*base_cmd, "check", "--fix", *py_files], check=False)
                    subprocess.run([*base_cmd, "format", *py_files], check=False)
                    total_formatted += len(py_files)
            elif fmt == "clang-format":
                cpp_files = [
                    str(f)
                    for f in files
                    if f.suffix.lower() in (".hxx", ".cxx", ".c", ".h", ".cpp")
                ]
                cf_bin = shutil.which("clang-format")
                if cpp_files and cf_bin:
                    _log(f"[{gname}] Formatting {len(cpp_files)} C/C++ file(s) with clang-format...")
                    subprocess.run([cf_bin, "-i", *cpp_files], check=False)
                    total_formatted += len(cpp_files)

    _log(f"✔ Source formatting complete across {len(group_names)} group(s).")
    sys.exit(0)


def cmd_check_doc(args):
    config = Config.load(args.config)
    _log("=" * 80)
    _log(f" Spec-Integrator: Document Verification Pipeline [{config.project.name}]")
    _log("=" * 80)
    files = getattr(args, "files", None)
    documents, graph, db, docs_root = _load_and_parse_all(
        config, clean=args.clean, file_paths=files
    )
    _log(f"✔ Parsed {len(documents)} document(s), {len(graph.nodes)} graph node(s).")

    # 1. Static Verifications (Format, Traceability, Hierarchy)
    _log("Running Static Verifiers (Format, Traceability, Hierarchy)...")
    issues = StaticVerifier(config).verify(documents, graph, docs_root)
    _log(f"Static verification finished. Found {len(issues)} issue(s).")

    # 2. Formal Verification (pyModelChecking Runner)
    _log("Running Formal Model Verifier...")
    formal_issues, formal_results = FormalVerifier(config).verify_documents(documents, docs_root)
    issues.extend(formal_issues)
    _log(f"Formal verification finished: {len(formal_results)} model(s) evaluated.")
    for r in formal_results:
        db.insert_formal_model(r.component, r.model_file, "pymodelchecking", r.status, r.details)

    # 3. WIT Interface Verification
    _log("Running WIT Interface Verifier...")
    wit_issues, wit_results = WITVerifier(config).verify_documents(documents, docs_root)
    issues.extend(wit_issues)
    _log(f"WIT verification finished: {len(wit_results)} file(s) evaluated.")
    for r in wit_results:
        db.insert_wit_file(
            r.component,
            r.wit_file,
            r.status,
            r.details,
            r.defined_interfaces,
            r.defined_worlds,
        )

    # 4. Evidence Verification
    _log("Running Evidence Verifier (unbacked claims & dangling artifacts)...")
    evidence_issues = EvidenceVerifier(config).verify(
        documents, docs_root, formal_results, wit_results
    )
    issues.extend(evidence_issues)
    _log(f"Evidence verification finished. Found {len(evidence_issues)} issue(s).")

    # 5. Obligation Verification
    _log("Running Obligation Verifier (skipped verification detection)...")
    obligation_issues, obligation_summary = ObligationVerifier(config).verify(documents, graph, db)
    issues.extend(obligation_issues)
    _log(
        f"Obligation verification finished: "
        f"{obligation_summary.discharged}/{obligation_summary.demanded} obligation(s) discharged."
    )

    # 6. Consistency Verification
    _log("Running Consistency Verifier (stale values, symbol drift, co-change)...")
    consistency_issues, consistency_summary = ConsistencyVerifier(config).verify(
        documents, docs_root, db=db
    )
    issues.extend(consistency_issues)
    _log(f"Consistency verification finished. Found {len(consistency_issues)} issue(s).")

    # 6.5 Terminology Variance Verification
    if getattr(config.terminology, "enabled", True):
        term_issues = TermVarianceJudge(config).generate_verification_issues(
            db, min_confidence=config.terminology.confidence_threshold
        )
        issues.extend(term_issues)
        _log(
            f"Terminology verification finished: "
            f"{len(term_issues)} term variance warning(s) detected."
        )

    # 6.6 Semantic Topic & Duplicate Verification (Sakura AI Embeddings)
    if getattr(config.semantic_topic, "enabled", True):
        topic_issues = SectionTopicVerifier(config).verify(db)
        issues.extend(topic_issues)
        _log(
            f"Semantic topic verification finished: "
            f"{len(topic_issues)} topic alignment/duplicate warning(s) detected."
        )

    # Persist all verification issues
    db.replace_verification_issues(issues)
    db.commit()

    # 7. Generate Report
    _log("Generating Markdown Report...")
    reporter = Reporter(config)
    report_path = Path(args.report).resolve()
    reporter.generate_markdown_report(
        documents,
        graph,
        issues,
        formal_results,
        wit_results,
        report_path,
        obligation_summary=obligation_summary,
        consistency_summary=consistency_summary,
        db=db,
    )
    _log(f"✔ Markdown Report generated: {_rel_path(report_path)}")

    db.close()

    # Evaluation
    errors = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]
    print("-" * 80)
    print(f" Verification Summary: {len(errors)} Error(s), {len(warnings)} Warning(s)")
    print("-" * 80)
    if errors:
        print("❌ QUALITY GATES FAILED:")
        for err in errors:
            print(f"  [{err.gate}] {err.file_path}:{err.line} - {err.message} ({err.rule_code})")
        sys.exit(1)

    print(
        f"✅ ALL QUALITY GATES PASSED "
        f"(verification obligations discharged: "
        f"{obligation_summary.discharged}/{obligation_summary.demanded})."
    )
    sys.exit(0)


def cmd_check_src(args):
    """Verifies source code against rules, anti-sabotage checks, and group configurations."""
    from spec_integrator.source_verifier import SourceVerifier

    config = Config.load(args.config)
    _log("=" * 80)
    _log(" Spec-Integrator: Source Verification (Anti-Sabotage & Language Rules)")
    _log("=" * 80)

    verifier = SourceVerifier(config)
    group = getattr(args, "group", None)
    group_names = verifier.resolve_group_names(group)
    explicit_files = getattr(args, "files", None)

    has_errors = False
    total_issues = 0

    for gname in group_names:
        files = verifier.collect_files_for_group(gname, explicit_files)
        _log(f"\n>>> Checking group '{gname}' ({len(files)} file(s))...")
        res = verifier.verify_group(gname, files)
        total_issues += len(res.issues)
        if res.issues:
            for iss in res.issues:
                prefix = "❌" if iss.severity == "ERROR" else "⚠️"
                print(f"  {prefix} [{iss.rule}] {iss.file_path}:{iss.line} - {iss.message}")
            if res.status == "FAIL":
                has_errors = True
        else:
            print(f"  ✔ Group '{gname}': All checks passed.")

    print("\n" + "=" * 80)
    if has_errors:
        print("❌ SOURCE VERIFICATION FAILED.")
        sys.exit(1)
    else:
        print(f"✅ ALL SOURCE CHECKS PASSED ({total_issues} warnings).")
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
    """Reviews single documents section-by-section and the connected islands of their high-risk keywords."""
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
    elif args.all:
        target_docs = documents
    else:
        print("Please specify a document target: --file <path> or --all.")
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
            if doc.file_path in isl.file_paths and isl.total_docs >= 2:
                if isl not in related_islands:
                    related_islands.append(isl)

        if related_islands:
            print(
                f"\n>>> [2/2] Reviewing {len(related_islands)} connected island(s) related to '{doc.file_path}' (high-risk kws: {len(high_risk_kws)})...",
                flush=True,
            )
            for idx, isl in enumerate(related_islands, start=1):
                print(
                    f"  [{idx}/{len(related_islands)}] Auditing Island '{isl.name}' ({isl.total_docs} docs)...",
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
    """Reviews connected document islands containing high-risk keywords."""
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
    kw_files = set()
    for kw in target_keywords:
        for doc in documents:
            if kw in doc.all_keywords:
                kw_files.add(doc.file_path)

    target_islands = [isl for isl in islands if any(f in kw_files for f in isl.file_paths)]
    print(
        f"Found {len(target_islands)} connected document island(s) associated with target keywords."
    )

    has_failures = False
    for idx, isl in enumerate(target_islands, start=1):
        print(
            f"\n[{idx}/{len(target_islands)}] Auditing Island '{isl.name}' ({isl.total_docs} docs, {isl.total_sections} sections)...",
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


def cmd_llm_word(args):
    """Executes terminology embedding, pairwise similarity indexing, LLM variance judgment, and report."""
    config = Config.load(args.config)
    documents, _graph, db, _docs_root = _load_and_parse_all(config)

    indexer = TermIndexer(config)
    _log(">>> [1/3] Generating term embeddings via Sakura AI...")
    new_embeddings = indexer.index_embeddings(db, model=args.model)
    _log(f"✔ Indexed {new_embeddings} new term embedding(s).")

    _log(">>> [2/3] Calculating pairwise similarities for terminology...")
    sim_pairs = indexer.compute_and_save_similarities(
        db, model=args.model, min_similarity=args.threshold
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
        choices=["openrouter", "sakura", "ollama", "mock"],
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
        "--backend", choices=["openrouter", "sakura", "ollama", "mock"], help="LLM backend"
    )
    p.add_argument("--model", help="LLM or embedding model name override")
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
        help="Skip LLM variance judgment and run static report",
    )
    p.set_defaults(func=cmd_llm_word)


def _add_llm_single_review_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-single-review",
        help="LLM review for single documents (section-by-section) and related high-risk keyword islands",
    )
    _add_config_arg(p)
    p.add_argument(
        "--file",
        help="Path to markdown document to review",
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
        choices=["openrouter", "sakura", "ollama", "mock"],
        help="LLM backend override",
    )
    p.add_argument("--model", help="LLM model name override")
    p.set_defaults(func=cmd_llm_single_review)


def _add_llm_keyword_review_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-keyword-review",
        help="LLM review for connected document islands containing high-risk keywords",
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
        choices=["openrouter", "sakura", "ollama", "mock"],
        help="LLM backend override",
    )
    p.add_argument("--model", help="LLM model name override")
    p.set_defaults(func=cmd_llm_keyword_review)


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
