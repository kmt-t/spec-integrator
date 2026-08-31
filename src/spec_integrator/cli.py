from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.graph import DocGraphBuilder
from spec_integrator.judge import RiskAssessor, SemanticJudge, TestChainJudge, TestChainReport
from spec_integrator.models import ParsedDocument
from spec_integrator.parser import MarkdownParser
from spec_integrator.reporter import Reporter
from spec_integrator.verifier import (
    ConsistencyVerifier,
    EvidenceVerifier,
    FormalVerifier,
    ObligationVerifier,
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


def _load_and_parse_all(config: Config, clean: bool = False):
    docs_root = config.get_docs_dir()
    if not docs_root.exists():
        print(f"[Error] Docs directory not found: {docs_root}", flush=True)
        sys.exit(1)

    db_path = config.get_db_path()
    db = DocAuditDB(db_path)
    if clean:
        db.clear_all()

    parser = MarkdownParser(config)
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
    pattern: '^[A-Za-z0-9_]+$'
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


def cmd_check(args):
    config = Config.load(args.config)
    _log("=" * 80)
    _log(f" Spec-Integrator: Document Verification Pipeline [{config.project.name}]")
    _log("=" * 80)
    documents, graph, db, docs_root = _load_and_parse_all(config, clean=args.clean)
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


def cmd_sync(args):
    """Records the current specification state as the consistency baseline in DB."""
    config = Config.load(args.config)
    documents, _graph, db, _docs_root = _load_and_parse_all(config)
    verifier = ConsistencyVerifier(config)
    baseline = verifier.build_baseline(documents)
    lock_path = verifier.write_baseline(documents)
    db.replace_consistency_baseline(baseline)
    db.commit()
    db.close()
    print(f"✔ Consistency baseline recorded in DB and {_rel_path(lock_path)}")
    print(
        f"  {len(baseline['sections'])} section(s), "
        f"{len(baseline['definitions'])} keyword definition(s), "
        f"{sum(len(v) for v in baseline['references'].values())} reference edge(s)."
    )
    print("  Commit this file. `check` compares against it to find edits that did not propagate.")
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


def _resolve_changed_sections(config: Config, documents, args, db) -> set[str]:
    cv = ConsistencyVerifier(config)
    lock_path = (
        Path(args.baseline) if args.baseline else config.resolve_path(config.consistency.lockfile)
    )
    baseline = cv.load_lock(lock_path)
    if baseline is None:
        print(
            f"❌ --changed-only requires a consistency baseline. "
            f"Run 'spec-integrator sync' first to create {lock_path}, "
            f"or pass --baseline pointing at one."
        )
        db.close()
        sys.exit(2)

    old_secs = baseline.get("sections", {})
    new_secs = cv.build_baseline(documents)["sections"]
    changed_sections = {sid for sid, h in new_secs.items() if old_secs.get(sid) != h}
    print(f"  ({len(changed_sections)} section(s) changed vs. {lock_path})")
    return changed_sections


def cmd_judge(args):
    config = Config.load(args.config)
    documents, graph, db, _docs_root = _load_and_parse_all(config)
    subgraphs = graph.extract_item_subgraphs()
    judge = SemanticJudge(config)
    changed_sections = (
        _resolve_changed_sections(config, documents, args, db) if args.changed_only else None
    )

    used_backend = args.backend or config.llm_judge.default_backend
    print(f"Running LLM as a Judge on candidate subgraphs (backend: {used_backend})...")
    report = judge.judge_subgraphs(
        subgraphs,
        documents,
        backend=args.backend,
        model=args.model,
        max_subgraphs=args.max_subgraphs,
        exhaustive=args.exhaustive or args.changed_only,
        min_references=args.min_references,
        changed_sections=changed_sections,
    )
    db.replace_judge_results([asdict(r) for r in report.results], used_backend)
    db.set_assessed_doc_hashes("judge", {d.file_path: d.content_hash for d in documents})
    db.commit()

    print(
        f"Judge finished. {report.total_evaluated} evaluated. "
        f"PASS: {report.pass_count}, WARN: {report.warn_count}, FAIL: {report.fail_count}"
    )

    # Whole-document self-consistency audit
    print(f"Running LLM as a Judge on whole documents (backend: {used_backend})...")
    doc_report = judge.judge_documents(
        documents,
        backend=args.backend,
        model=args.model,
        max_documents=args.max_documents,
        exhaustive=args.exhaustive,
    )
    db.replace_document_judge_results([asdict(r) for r in doc_report.results], used_backend)
    db.set_assessed_doc_hashes("document_judge", {d.file_path: d.content_hash for d in documents})
    db.commit()
    print(
        f"Document judge finished. {doc_report.total_evaluated} evaluated. "
        f"PASS: {doc_report.pass_count}, WARN: {doc_report.warn_count}, FAIL: {doc_report.fail_count}"
    )

    # 3-tier traceability chain audit
    tc_report = _run_test_chain_audit(config, db, args)

    db.close()
    sys.exit(
        1 if (report.fail_count > 0 or doc_report.fail_count > 0 or tc_report.fail_count > 0) else 0
    )


def _run_test_chain_audit(config: Config, db: DocAuditDB, args) -> TestChainReport:
    test_judge = TestChainJudge(config)
    all_targets = test_judge.auto_discover_targets()
    if args.component:
        targets = [
            t
            for t in all_targets
            if t.component_name == args.component or args.component in t.component_name
        ]
        if not targets:
            print(
                f"[Error] No matching component found for '{args.component}'. "
                f"Available: {', '.join(t.component_name for t in all_targets)}"
            )
            db.close()
            sys.exit(1)
    else:
        targets = all_targets

    max_targets = 0 if args.exhaustive else args.max_targets
    backend = args.backend or config.llm_judge.default_backend
    report = test_judge.judge_targets(
        targets, backend=backend, model=args.model, max_targets=max_targets
    )

    db.replace_test_chain_results([asdict(r) for r in report.results], backend)
    db.commit()

    print("\n" + report.to_markdown(project_name=config.project.name or "System Specification"))
    print("Verdicts recorded in the cache DB; see 'check' report § Test Chain Verdicts.")
    return report


def cmd_assess(args):
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


def _add_check_subparser(subparsers) -> None:
    p = subparsers.add_parser("check", help="Run static & formal document verification pipeline")
    _add_config_arg(p)
    p.add_argument("-r", "--report", default="spec_report.md", help="Markdown report output path")
    p.add_argument("--clean", action="store_true", help="Clear cache DB and run clean audit")
    p.set_defaults(func=cmd_check)


def _add_sync_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "sync", help="Record the current spec state as the consistency baseline (lockfile)"
    )
    _add_config_arg(p)
    p.set_defaults(func=cmd_sync)


def _add_graph_subparser(subparsers) -> None:
    p = subparsers.add_parser("graph", help="Extract and visualize DocGraph")
    _add_config_arg(p)
    p.add_argument(
        "-f", "--format", choices=["mermaid", "json"], default="mermaid", help="Output format"
    )
    p.add_argument("-o", "--out", help="Output file path")
    p.set_defaults(func=cmd_graph)


def _add_judge_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-judge",
        help="Run LLM as a Judge on semantic subgraphs, whole documents, and 3-tier test chain",
    )
    _add_config_arg(p)
    p.add_argument(
        "--backend", choices=["openrouter", "sakura", "ollama", "mock"], help="LLM backend"
    )
    p.add_argument("--model", help="LLM model name override")
    p.add_argument(
        "--component",
        help="Limit 3-tier chain audit to one component (e.g. 'jit_compiler').",
    )
    p.add_argument(
        "--max-subgraphs",
        type=int,
        default=10,
        help="Max requirement subgraphs to evaluate (0 for unlimited).",
    )
    p.add_argument(
        "--max-documents",
        type=int,
        default=15,
        help="Max whole documents to evaluate (0 for unlimited).",
    )
    p.add_argument(
        "--max-targets",
        type=int,
        default=10,
        help="Max components in 3-tier test chain audit (0 for unlimited).",
    )
    p.add_argument(
        "-a",
        "--exhaustive",
        action="store_true",
        help="Exhaustive audit across all subgraphs, documents, and test chains.",
    )
    p.add_argument(
        "--min-references",
        type=int,
        default=1,
        help="Minimum referencing sections required to include a subgraph (default: 1).",
    )
    p.add_argument(
        "--changed-only",
        action="store_true",
        help="Only audit subgraphs touching a section that changed since last sync.",
    )
    p.add_argument(
        "--baseline",
        metavar="LOCKFILE",
        help="Lockfile to diff against for --changed-only.",
    )
    p.set_defaults(func=cmd_judge)


def _add_assess_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-assess",
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
    p.set_defaults(func=cmd_assess)


_SUBPARSER_BUILDERS = (
    _add_init_subparser,
    _add_check_subparser,
    _add_sync_subparser,
    _add_graph_subparser,
    _add_judge_subparser,
    _add_assess_subparser,
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
