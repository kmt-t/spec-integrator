import sys
import argparse
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
from spec_integrator.judge.llm import LLMJudge
from spec_integrator.reporter import Reporter


def cmd_init(args):
    target = Path("spec-integrator.yaml")
    if target.exists():
        print(f"[Error] '{target}' already exists.")
        sys.exit(1)

    template = """version: "1.0"

project:
  name: "My Specification Project"
  docs_root: "docs"
  cache_db: ".spec-integrator/doc_cache.db"

tiers:
  - tier: 0
    name: "Requirements"
    path_pattern: "docs/requires/**/*.md"
    description: "System Requirements"

  - tier: 1
    name: "Core"
    path_pattern: "docs/components/tier1_*/**/*.md"
    description: "Core System Components"

  - tier: 2
    name: "Runtime"
    path_pattern: "docs/components/tier2_*/**/*.md"
    description: "Runtime & Execution Engine"

  - tier: 3
    name: "Platform"
    path_pattern: "docs/components/tier3_*/**/*.md"
    description: "Platform Abstraction & Drivers"

  - tier: "meta"
    name: "Architecture & Plans"
    path_pattern: "docs/{architecture,plans}/**/*.md"
    description: "Architecture & Plans"

keywords:
  meta:
    pattern: "^META_[A-Za-z0-9_]+$"
    defined_in: "docs/architecture/document_structure.md"
  global:
    pattern: "^GLOBAL_[A-Za-z0-9_]+$"
    defined_in: "docs/architecture/document_structure.md"
  local:
    pattern: "^[A-Za-z0-9_]+$"
    defined_in: "docs/requires/**/*.md"

formal_verification:
  model_dir_name: "formal"
  tag: "{VERIFY_FORMAL}"
  timeout_seconds: 30

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
    md_files = sorted(list(docs_root.rglob("*.md")))

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

    # 3. Generate Report
    print("Generating Markdown Report & Graph JSON...", flush=True)
    report_path = Path(args.report).resolve()
    reporter = Reporter(config)
    reporter.generate_markdown_report(documents, graph, issues, formal_results, report_path)
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
        print("✅ ALL QUALITY GATES PASSED.")
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
    judge = LLMJudge(config)

    print(f"Running LLM as a Judge on candidate subgraphs (backend: {args.backend or config.llm_judge.default_backend})...")
    results = judge.judge_subgraphs(
        subgraphs, documents,
        backend=args.backend,
        model=args.model,
        max_subgraphs=args.max_subgraphs
    )

    db.close()

    if args.out:
        out_p = Path(args.out).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            import json
            json.dump([asdict(r) for r in results], f, indent=2, ensure_ascii=False)
        print(f"✔ Judge report saved to {out_p}")

    has_fail = any(r.status == "FAIL" for r in results)
    print(f"Judge finished. {len(results)} evaluated. Fails: {sum(1 for r in results if r.status == 'FAIL')}")
    sys.exit(1 if has_fail else 0)


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
    p_judge.add_argument("--max-subgraphs", type=int, default=10, help="Max subgraphs to evaluate")
    p_judge.add_argument("-o", "--out", default="judge_report.json", help="Output JSON path")
    p_judge.set_defaults(func=cmd_judge)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
