import sys
import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.parser import MarkdownParser
from spec_integrator.graph import DocGraphBuilder
from spec_integrator.verifier.static import StaticVerifier
from spec_integrator.verifier.formal import FormalVerifier
from spec_integrator.verifier.wit import WITVerifier
from spec_integrator.verifier.evidence import EvidenceVerifier
from spec_integrator.verifier.obligation import ObligationVerifier
from spec_integrator.verifier.consistency import ConsistencyVerifier
from spec_integrator.verifier.topology import TopologyVerifier
from spec_integrator.judge import SemanticJudge, RiskAssessor
from spec_integrator.reporter import Reporter


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
  # A model that cannot fail is not a proof. Each model must expose
  # build_model() and properties(); safety properties must declare the
  # violating condition so vacuity can be ruled out.
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

# Evidence Gate: a document may not assert a verification it cannot substantiate.
evidence:
  enabled: true
  metric_severity: "WARNING"   # raise to ERROR to forbid unsourced figures outright
  ignore_artifact_refs: []

# Obligation Gate: verification demanded by `assess` may not be silently skipped.
obligation:
  enabled: true
  risk_report: "reports/doc_risk_report.json"
  judge_report: "reports/doc_judge_report.json"
  require_assessment: true     # `check` without a prior `assess` is not a pass
  require_judge: true
  risk_threshold: 4
  stale_is_error: true
"""
    target.write_text(template, encoding="utf-8")
    print(f"✔ Created '{target}'.")
    sys.exit(0)


def _load_and_parse_all(config: Config, clean: bool = False):
    docs_root = config.get_docs_dir()
    if not docs_root.exists():
        print(f"[Error] Docs directory not found: {docs_root}", flush=True)
        sys.exit(1)

    db_path = config.get_db_path()
    if clean and db_path.exists():
        db_path.unlink()

    db = DocAuditDB(db_path)
    if clean:
        db.clear_all()

    parser = MarkdownParser(config)
    all_md = sorted(list(docs_root.rglob("*.md")))
    md_files = [f for f in all_md if not config.is_excluded(f, docs_root)]

    print(f"Scanning {len(md_files)} markdown files in {docs_root}...", flush=True)
    documents = []
    for idx, md_file in enumerate(md_files, 1):
        doc = parser.parse_file(md_file, docs_root)
        documents.append(doc)

        # Store to DB
        db.insert_document(doc.file_path, doc.tier, doc.component, doc.content_hash)
        for sec in doc.sections:
            sec_hash = parser.config_compute_hash(sec.body_text)
            db.insert_section(
                sec.section_id, doc.file_path, sec.heading, sec.level,
                sec.line_start, sec.line_end, sec.body_text, sec_hash
            )
            for kw in sec.keywords:
                is_def = "defines" if config.is_keyword_definition(kw, doc.file_path) else "refers_to"
                db.insert_keyword_reference(kw, doc.file_path, sec.section_id, is_def, sec.line_start)

        for link in doc.all_links:
            db.insert_link(link.source_file, link.source_line, link.target_path, link.target_anchor, 1)

    db.commit()

    print("Building DocGraph topology...", flush=True)
    graph_builder = DocGraphBuilder(config)
    graph = graph_builder.build(documents, docs_root)
    print(f"DocGraph built: {len(graph.nodes)} nodes, {len(graph.edges)} edges.", flush=True)

    return documents, graph, db, docs_root


def cmd_check(args):
    config_path = args.config
    config = Config.load(config_path)

    print("================================================================================", flush=True)
    print(f" Spec-Integrator: Document Verification Pipeline [{config.project.name}]", flush=True)
    print("================================================================================", flush=True)

    documents, graph, db, docs_root = _load_and_parse_all(config, clean=args.clean)
    print(f"✔ Parsed {len(documents)} document(s), {len(graph.nodes)} graph node(s).", flush=True)

    # 1. Static Verifications (Format, Traceability, Hierarchy)
    print("Running Static Verifiers (Format, Traceability, Hierarchy)...", flush=True)
    static_verifier = StaticVerifier(config)
    issues = static_verifier.verify(documents, graph, docs_root)
    print(f"Static verification finished. Found {len(issues)} issue(s).", flush=True)

    # 2. Formal Verification (pyModelChecking Runner)
    print("Running Formal Model Verifier...", flush=True)
    formal_verifier = FormalVerifier(config)
    formal_issues, formal_results = formal_verifier.verify_documents(documents, docs_root)
    issues.extend(formal_issues)
    print(f"Formal verification finished: {len(formal_results)} model(s) evaluated.", flush=True)

    # Save formal model results to DB
    for r in formal_results:
        db.insert_formal_model(r.component, r.model_file, "pymodelchecking", r.status, r.details)

    # 3. WIT Interface Verification
    print("Running WIT Interface Verifier...", flush=True)
    wit_verifier = WITVerifier(config)
    wit_issues, wit_results = wit_verifier.verify_documents(documents, docs_root)
    issues.extend(wit_issues)
    print(f"WIT verification finished: {len(wit_results)} file(s) evaluated.", flush=True)

    # 4. Evidence Verification (claims must be substantiated)
    print("Running Evidence Verifier (unbacked claims & dangling artifacts)...", flush=True)
    evidence_verifier = EvidenceVerifier(config)
    evidence_issues = evidence_verifier.verify(documents, docs_root, formal_results, wit_results)
    issues.extend(evidence_issues)
    print(f"Evidence verification finished. Found {len(evidence_issues)} issue(s).", flush=True)

    # 5. Obligation Verification (risk assessment must not be ignored)
    print("Running Obligation Verifier (skipped verification detection)...", flush=True)
    obligation_verifier = ObligationVerifier(config)
    obligation_issues, obligation_summary = obligation_verifier.verify(documents)
    issues.extend(obligation_issues)
    print(f"Obligation verification finished: "
          f"{obligation_summary.discharged}/{obligation_summary.demanded} obligation(s) discharged.",
          flush=True)

    # 6. Consistency Verification (edits must reach every restatement of a fact)
    print("Running Consistency Verifier (stale values, symbol drift, co-change)...", flush=True)
    consistency_verifier = ConsistencyVerifier(config)
    consistency_issues, consistency_summary = consistency_verifier.verify(documents, docs_root)
    issues.extend(consistency_issues)
    print(f"Consistency verification finished. Found {len(consistency_issues)} issue(s).", flush=True)

    # 7. Topology Verification (circular wait & deadlock freedom in messaging graphs)
    print("Running Topology Verifier (static acyclic channel & messaging topology)...", flush=True)
    topology_verifier = TopologyVerifier(config)
    topology_issues, topology_results = topology_verifier.verify_documents(documents, docs_root)
    issues.extend(topology_issues)
    print(f"Topology verification finished: {len(topology_results)} topology graph(s) evaluated.", flush=True)

    # 8. Generate Report
    print("Generating Markdown Report & Graph JSON...", flush=True)
    report_path = Path(args.report).resolve()
    reporter = Reporter(config)
    reporter.generate_markdown_report(documents, graph, issues, formal_results, wit_results,
                                      report_path, obligation_summary=obligation_summary,
                                      consistency_summary=consistency_summary,
                                      topology_results=topology_results)
    print(f"✔ Markdown Report generated: {report_path}", flush=True)

    if args.graph_json:
        graph_json_path = Path(args.graph_json).resolve()
        reporter.export_graph_json(graph, graph_json_path)
        print(f"✔ Graph JSON exported: {graph_json_path}", flush=True)

    db.close()

    # Evaluation
    errors = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]

    print("--------------------------------------------------------------------------------")
    print(f" Verification Summary: {len(errors)} Error(s), {len(warnings)} Warning(s)")
    print("--------------------------------------------------------------------------------")

    if errors:
        print("❌ QUALITY GATES FAILED:")
        for err in errors:
            print(f"  [{err.gate}] {err.file_path}:{err.line} - {err.message} ({err.rule_code})")
        sys.exit(1)
    else:
        print(f"✅ ALL QUALITY GATES PASSED "
              f"(verification obligations discharged: "
              f"{obligation_summary.discharged}/{obligation_summary.demanded}).")
        sys.exit(0)


def cmd_sync(args):
    """Records the current specification state as the consistency baseline."""
    config = Config.load(args.config)
    documents, graph, db, docs_root = _load_and_parse_all(config)

    verifier = ConsistencyVerifier(config)
    baseline = verifier.build_baseline(documents)
    lock_path = verifier.write_baseline(documents)
    db.close()

    print(f"✔ Consistency baseline written: {lock_path}")
    print(f"  {len(baseline['sections'])} section(s), "
          f"{len(baseline['definitions'])} keyword definition(s), "
          f"{sum(len(v) for v in baseline['references'].values())} reference edge(s).")
    print("  Commit this file. `check` compares against it to find edits that did not propagate.")
    sys.exit(0)


def cmd_graph(args):
    config_path = args.config
    config = Config.load(config_path)
    documents, graph, db, docs_root = _load_and_parse_all(config)

    fmt = args.format.lower()
    if fmt == "mermaid":
        content = graph.to_mermaid()
    elif fmt == "json":
        import json
        content = json.dumps(graph.to_dict(), indent=2, ensure_ascii=False)
    else:
        content = graph.to_mermaid()

    if args.out:
        out_p = Path(args.out).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(content, encoding="utf-8")
        print(f"✔ Graph saved to {out_p}")
    else:
        print(content)

    db.close()
    sys.exit(0)


def cmd_judge(args):
    config_path = args.config
    config = Config.load(config_path)
    documents, graph, db, docs_root = _load_and_parse_all(config)

    subgraphs = graph.extract_item_subgraphs()
    judge = SemanticJudge(config)

    changed_sections = None
    if args.changed_only:
        from spec_integrator.verifier.consistency import ConsistencyVerifier
        cv = ConsistencyVerifier(config)
        if args.baseline:
            lock_path = Path(args.baseline)
            baseline_label = str(lock_path)
        else:
            lock_path = config.resolve_path(config.consistency.lockfile)
            baseline_label = str(lock_path)
        baseline = cv._load_lock(lock_path)
        if baseline is None:
            print(f"❌ --changed-only requires a consistency baseline. "
                  f"Run 'spec-integrator sync' first to create {lock_path}, "
                  f"or pass --baseline pointing at one.")
            db.close()
            sys.exit(2)
        old_secs = baseline.get("sections", {})
        current = cv.build_baseline(documents)
        new_secs = current["sections"]
        # A section with no prior hash is new; either way it differs from
        # whatever the baseline recorded (or didn't).
        changed_sections = {sid for sid, h in new_secs.items() if old_secs.get(sid) != h}
        print(f"  ({len(changed_sections)} section(s) changed vs. {baseline_label})")

    print(f"Running LLM as a Judge on candidate subgraphs (backend: {args.backend or config.llm_judge.default_backend})...")
    report = judge.judge_subgraphs(
        subgraphs, documents,
        backend=args.backend,
        model=args.model,
        max_subgraphs=args.max_subgraphs,
        exhaustive=args.exhaustive or args.changed_only,
        min_references=args.min_references,
        changed_sections=changed_sections,
    )

    db.close()

    if args.out:
        out_p = Path(args.out).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            import json
            # Record the content hash of every document this verdict was formed
            # against. Without it a committed judge report keeps reading as a
            # current audit no matter how far the specification moves on, and a
            # stale opinion is indistinguishable from a fresh pass.
            json.dump({
                "results": [asdict(r) for r in report.results],
                "doc_hashes": {d.file_path: d.content_hash for d in documents},
            }, f, indent=2, ensure_ascii=False)
        print(f"✔ Judge report JSON saved to {out_p}")

    if args.report:
        rep_p = Path(args.report).resolve()
        rep_p.parent.mkdir(parents=True, exist_ok=True)
        rep_p.write_text(report.to_markdown(), encoding="utf-8")
        print(f"✔ Judge Markdown report saved to {rep_p}")

    has_fail = report.fail_count > 0
    print(f"Judge finished. {report.total_evaluated} evaluated. PASS: {report.pass_count}, WARN: {report.warn_count}, FAIL: {report.fail_count}")
    sys.exit(1 if has_fail else 0)


def cmd_assess(args):
    config_path = args.config
    config = Config.load(config_path)
    documents, graph, db, docs_root = _load_and_parse_all(config)

    target_tiers = [t.strip() for t in args.tier.split(",")] if args.tier else None

    assessor = RiskAssessor(config)
    print(f"Running Content Complexity & Risk Assessment (backend: {args.backend or config.llm_judge.default_backend})...")
    report = assessor.assess_documents(
        documents,
        backend=args.backend,
        model=args.model,
        max_sections=args.max_sections,
        exhaustive=args.exhaustive,
        min_length=args.min_length,
        include_meta=args.include_meta or args.exhaustive,
        include_reqs=args.include_reqs or args.exhaustive,
        target_tiers=target_tiers,
    )

    db.close()

    # Record which document revision each obligation was derived from, so that
    # `check` can detect an assessment that no longer matches the specification.
    doc_hashes = {d.file_path: d.content_hash for d in documents}

    if args.out:
        out_p = Path(args.out).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                # Which engine produced these obligations. A mock backend derives the
                # verdict from the document itself, so obligations it generates cannot
                # be evidence about the document.
                "backend": args.backend or config.llm_judge.default_backend,
                "total_evaluated": report.total_evaluated,
                "formal_candidates_count": report.formal_candidates_count,
                "llm_candidates_count": report.llm_candidates_count,
                "sections_scanned": sum(len(d.sections) for d in documents),
                "max_sections": args.max_sections,
                "doc_hashes": doc_hashes,
                "assessments": [asdict(a) for a in report.assessments]
            }, f, indent=2, ensure_ascii=False)
        print(f"✔ Risk assessment JSON saved to {out_p}")

    if args.report:
        rep_p = Path(args.report).resolve()
        rep_p.parent.mkdir(parents=True, exist_ok=True)
        rep_p.write_text(report.to_markdown(), encoding="utf-8")
        print(f"✔ Risk assessment Markdown report saved to {rep_p}")

    print(f"\nAssessment finished. Evaluated {report.total_evaluated} sections.")
    print(f"  - Formal verification (pyModelChecking) candidates: {report.formal_candidates_count}")
    print(f"  - LLM Judge candidates: {report.llm_candidates_count}")

    # An assessment that only covered part of the corpus silently under-reports
    # the obligations, which is exactly how required verification gets skipped.
    total_sections = sum(len(d.sections) for d in documents)
    if args.strict and report.total_evaluated < total_sections:
        print(f"\n❌ Partial assessment: {report.total_evaluated}/{total_sections} sections evaluated.")
        print("   Obligations for the unevaluated sections are unknown, so the result is not a "
              "clean bill of health. Raise --max-sections, or pass --no-strict to accept it.")
        sys.exit(1)

    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        prog="spec-integrator",
        description="Universal Document Quality, Traceability, Formal Verification & LLM Judge Tool"
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # init
    p_init = subparsers.add_parser("init", help="Initialize spec-integrator.yaml configuration")
    p_init.set_defaults(func=cmd_init)

    # check
    p_check = subparsers.add_parser("check", help="Run static & formal document verification pipeline")
    p_check.add_argument("-c", "--config", default="spec-integrator.yaml", help="Path to configuration file")
    p_check.add_argument("-r", "--report", default="spec_report.md", help="Markdown report output path")
    p_check.add_argument("-g", "--graph-json", default="graph.json", help="Graph JSON output path")
    p_check.add_argument("--clean", action="store_true", help="Clear cache DB and run clean audit")
    p_check.set_defaults(func=cmd_check)

    # sync
    p_sync = subparsers.add_parser(
        "sync", help="Record the current spec state as the consistency baseline (lockfile)")
    p_sync.add_argument("-c", "--config", default="spec-integrator.yaml", help="Path to configuration file")
    p_sync.set_defaults(func=cmd_sync)

    # graph
    p_graph = subparsers.add_parser("graph", help="Extract and visualize DocGraph")
    p_graph.add_argument("-c", "--config", default="spec-integrator.yaml", help="Path to configuration file")
    p_graph.add_argument("-f", "--format", choices=["mermaid", "json"], default="mermaid", help="Output format")
    p_graph.add_argument("-o", "--out", help="Output file path")
    p_graph.set_defaults(func=cmd_graph)

    # judge
    p_judge = subparsers.add_parser("judge", help="Run LLM as a Judge on semantic subgraphs")
    p_judge.add_argument("-c", "--config", default="spec-integrator.yaml", help="Path to configuration file")
    p_judge.add_argument("--backend", choices=["sakura", "ollama", "mock"], help="LLM backend")
    p_judge.add_argument("--model", help="LLM model name override")
    p_judge.add_argument("--max-subgraphs", type=int, default=10, help="Max subgraphs to evaluate (0 for unlimited)")
    p_judge.add_argument("-a", "--all", "--exhaustive", dest="exhaustive", action="store_true",
                         help="Exhaustive audit: check all requirement subgraphs regardless of {VERIFY_LLM} tag")
    p_judge.add_argument("--min-references", type=int, default=1,
                         help="Minimum referencing sections required to include a subgraph (default: 1, 0 for all)")
    p_judge.add_argument("--changed-only", action="store_true",
                         help="Only audit subgraphs touching a section that changed since the last "
                              "'spec-integrator sync' (per spec-consistency.lock). Cheap enough to run "
                              "on every edit; catches drift regardless of which side of a definition/"
                              "reference pair moved, and regardless of {VERIFY_LLM} tagging.")
    p_judge.add_argument("--baseline", metavar="LOCKFILE",
                         help="Lockfile to diff against for --changed-only, instead of the live "
                              "spec-consistency.lock. In CI the working-tree lockfile has already "
                              "absorbed this PR's own edits (authors run 'sync' before committing), so "
                              "comparing against it finds nothing changed. Pass a checkout of the base "
                              "branch's lockfile here instead, e.g. "
                              "'git show origin/main:spec-consistency.lock > /tmp/base.lock'.")
    p_judge.add_argument("-o", "--out", default="judge_report.json", help="Output JSON path")
    p_judge.add_argument("-r", "--report", default="reports/doc_judge_report.md", help="Markdown report output path")
    p_judge.set_defaults(func=cmd_judge)

    # assess
    p_assess = subparsers.add_parser("assess", help="Assess section complexity, design risk & formal candidates via LLM")
    p_assess.add_argument("-c", "--config", default="spec-integrator.yaml", help="Path to configuration file")
    p_assess.add_argument("--backend", choices=["sakura", "ollama", "heuristic", "static_rule", "mock"], help="Risk assessor backend")
    p_assess.add_argument("--model", help="LLM model name override")
    p_assess.add_argument("--max-sections", type=int, default=15, help="Max sections to assess (0 for unlimited)")
    p_assess.add_argument("-a", "--all", "--exhaustive", dest="exhaustive", action="store_true",
                          help="Exhaustive assessment: evaluate all sections across all tiers including Requirements and Meta")
    p_assess.add_argument("--min-length", type=int, default=50, help="Minimum body character length to evaluate (default: 50)")
    p_assess.add_argument("--include-meta", action="store_true", help="Include Architecture and Meta tier in candidate selection")
    p_assess.add_argument("--include-reqs", action="store_true", help="Include Tier 0 (Requirements) in candidate selection")
    p_assess.add_argument("--tier", help="Comma-separated target tiers to assess (e.g. '0,1,2')")
    p_assess.add_argument("-o", "--out", default="doc_risk_report.json", help="Output JSON path")
    p_assess.add_argument("-r", "--report", default="doc_risk_report.md", help="Output Markdown report path")
    p_assess.add_argument("--strict", dest="strict", action="store_true", default=True,
                          help="Fail when the assessment does not cover every section (default)")
    p_assess.add_argument("--no-strict", dest="strict", action="store_false",
                          help="Allow a partial assessment to exit successfully")
    p_assess.set_defaults(func=cmd_assess)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
