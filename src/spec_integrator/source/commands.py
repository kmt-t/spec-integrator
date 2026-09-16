"""Source-oriented command handlers."""

from __future__ import annotations

import sys

from spec_integrator.config import Config
from spec_integrator.source import SourceFacade


def _log(message: str) -> None:
    print(message, flush=True)


def cmd_format_src(args) -> None:
    """Format source files with the configured source formatters."""
    config = Config.load(args.config)
    _log("=" * 80)
    _log(" Spec-Integrator: Source Formatter")
    _log("=" * 80)

    total_formatted = SourceFacade(config).format(
        group=getattr(args, "group", None),
        file_paths=getattr(args, "files", None),
    )
    _log(f"✔ Source formatting complete: {total_formatted} file(s).")
    sys.exit(0)


def cmd_check_src(args) -> None:
    """Verify source files against language and anti-sabotage rules."""
    config = Config.load(args.config)
    _log("=" * 80)
    _log(" Spec-Integrator: Source Verification (Anti-Sabotage & Language Rules)")
    _log("=" * 80)

    has_errors = False
    total_issues = 0
    results = SourceFacade(config).check(
        group=getattr(args, "group", None),
        file_paths=getattr(args, "files", None),
    )
    for result in results:
        _log(f"\n>>> Checking group '{result.group}' ({result.files_evaluated} file(s))...")
        total_issues += len(result.issues)
        if result.issues:
            for issue in result.issues:
                prefix = "❌" if issue.severity == "ERROR" else "⚠️"
                print(f"  {prefix} [{issue.rule}] {issue.file_path}:{issue.line} - {issue.message}")
            if result.status == "FAIL":
                has_errors = True
        else:
            print(f"  ✔ Group '{result.group}': All checks passed.")

    print("\n" + "=" * 80)
    if has_errors:
        print("❌ SOURCE VERIFICATION FAILED.")
        sys.exit(1)
    print(f"✅ ALL SOURCE CHECKS PASSED ({total_issues} warnings).")
    sys.exit(0)


__all__ = ["cmd_check_src", "cmd_format_src"]
