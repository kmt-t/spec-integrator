"""Document-oriented command handlers."""

from __future__ import annotations

import sys
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.document import DocumentFacade


def _log(message: str) -> None:
    print(message, flush=True)


def cmd_build(args) -> None:
    """Build the document database and terminology index."""
    config = Config.load(args.config)
    _log("=" * 80)
    _log(f" Spec-Integrator: Building Document Database [{config.project.name}]")
    _log("=" * 80)
    document = DocumentFacade(config)
    workspace = document.load_workspace(
        clean=getattr(args, "clean", False),
        file_paths=getattr(args, "files", None),
    )
    _log(
        f"✔ Parsed {len(workspace.documents)} document(s), "
        f"{len(workspace.graph.nodes)} node(s)."
    )
    _log("Extracting terminology & keywords via TF-IDF...")
    terms_count = document.build_terms(workspace)
    _log(f"✔ Extracted and indexed {terms_count} terms via TF-IDF.")

    workspace.db.close()
    _log("=" * 80)
    _log(f"✔ Database build complete: {config.get_db_path()}")
    _log("=" * 80)
    sys.exit(0)


def cmd_format_doc(args) -> None:
    """Format Markdown documents."""
    config = Config.load(args.config)
    _log("=" * 80)
    _log(" Spec-Integrator: Document Formatter (Markdown)")
    _log("=" * 80)
    formatted_count, examined_count = DocumentFacade(config).format(
        file_paths=getattr(args, "files", None)
    )
    _log(
        f"✔ Document formatting complete. "
        f"Normalized {formatted_count}/{examined_count} file(s)."
    )
    sys.exit(0)


def cmd_check_doc(args) -> None:
    """Run all document quality gates, including prose readability."""
    config = Config.load(args.config)
    _log("=" * 80)
    _log(f" Spec-Integrator: Document Verification Pipeline [{config.project.name}]")
    _log("=" * 80)
    document = DocumentFacade(config)
    workspace = document.load_workspace(
        clean=args.clean,
        file_paths=getattr(args, "files", None),
    )
    result = document.check(workspace, Path(args.report).resolve())
    workspace.db.close()

    errors = result.errors
    warnings = [issue for issue in result.issues if issue.severity == "WARNING"]
    print("-" * 80)
    print(f" Verification Summary: {len(errors)} Error(s), {len(warnings)} Warning(s)")
    print("-" * 80)
    if errors:
        print("❌ QUALITY GATES FAILED:")
        for error in errors:
            print(
                f"  [{error.gate}] {error.file_path}:{error.line} - "
                f"{error.message} ({error.rule_code})"
            )
        sys.exit(1)
    print(
        f"✅ ALL QUALITY GATES PASSED (verification obligations discharged: "
        f"{result.obligation_summary.discharged}/{result.obligation_summary.demanded})."
    )
    sys.exit(0)


__all__ = ["cmd_build", "cmd_check_doc", "cmd_format_doc"]
