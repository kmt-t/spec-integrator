"""Command-line argument definitions."""

from __future__ import annotations

import argparse

from spec_integrator.application.commands.core import cmd_graph, cmd_init, cmd_risk
from spec_integrator.application.commands.llm.findings import cmd_llm_findings
from spec_integrator.application.commands.llm.judge import cmd_llm_judge
from spec_integrator.application.commands.llm.keyword_review import cmd_llm_keyword_review
from spec_integrator.application.commands.llm.single_review import cmd_llm_single_review
from spec_integrator.application.commands.llm.word import cmd_llm_word
from spec_integrator.document.commands import cmd_build, cmd_check_doc, cmd_format_doc
from spec_integrator.source.commands import cmd_check_src, cmd_format_src

CHECKSHEET_BACKENDS = ("jev", "clef-flash", "mock")


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
    p.add_argument(
        "-r", "--report", default="reports/doc_report.md", help="Markdown report output path"
    )
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
        "check-src", help="Verify source code (automated gate, language rules, test execution)"
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
        choices=CHECKSHEET_BACKENDS,
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
        choices=CHECKSHEET_BACKENDS,
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
        help="LLM review for single documents (section-by-section) and related high-risk keyword link pairs",
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
        help="Override high risk threshold for keyword link pairs",
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
        choices=CHECKSHEET_BACKENDS,
        help="LLM backend override",
    )
    p.add_argument("--model", help="LLM model name override")
    p.set_defaults(func=cmd_llm_single_review)


def _add_llm_keyword_review_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-keyword-review",
        help="LLM review for high-risk keyword definition/reference section pairs",
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
        choices=CHECKSHEET_BACKENDS,
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
        "--max-keyword-groups",
        type=int,
        default=20,
        help="Max keyword groups to audit in link-pair mode (default: 20, 0 for unlimited).",
    )
    p.add_argument(
        "-a",
        "--exhaustive",
        action="store_true",
        help="Ignore --max-documents/--max-keyword-groups and audit full coverage.",
    )
    p.add_argument(
        "--check",
        help="Run only a specific check ID",
    )
    p.add_argument(
        "--list-checks",
        action="store_true",
        help="List all configured single-section/link-pair review checks and exit",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and display prompts without calling the LLM backend or persisting results",
    )
    p.add_argument(
        "--backend",
        choices=CHECKSHEET_BACKENDS,
        help="LLM backend override",
    )
    p.add_argument("--model", help="LLM model name override")
    p.set_defaults(func=cmd_llm_judge)


def _add_llm_findings_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "llm-findings",
        help="Query persisted LLM review decisions by backend-specific score and outcome",
    )
    _add_config_arg(p)
    p.add_argument(
        "--min-confidence",
        type=float,
        default=0.70,
        help="Minimum backend-specific score from 0.0 to 1.0 (default: 0.70)",
    )
    p.add_argument(
        "--backend",
        choices=CHECKSHEET_BACKENDS,
        help="Filter by backend (defaults to the configured LLM backend)",
    )
    p.add_argument(
        "--classification",
        action="append",
        choices=[
            "confirmed_violation",
            "possible_violation",
            "documented_open_issue",
            "insufficient_context",
            "improvement_suggestion",
            "no_issue",
        ],
        help="Filter by outcome; may be repeated. Defaults to confirmed/possible violations.",
    )
    p.add_argument(
        "--all-outcomes",
        action="store_true",
        help="Include all outcomes, including no_issue and insufficient_context",
    )
    p.add_argument(
        "--run-type",
        choices=[
            "llm-single-review",
            "llm-single-review-link-pair",
            "llm-keyword-review",
            "llm-judge-document",
            "llm-judge-link-pair",
            "imported-level2-audit-log",
        ],
        help="Filter to one review command or mode",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum rows to print; use 0 for all matches (default: 200)",
    )
    p.set_defaults(func=cmd_llm_findings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spec-integrator",
        description="Universal Document Quality, Traceability, Formal Verification & LLM Judge Tool",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")
    builders = (
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
        _add_llm_findings_subparser,
    )
    for build_subparser in builders:
        build_subparser(subparsers)
    return parser
