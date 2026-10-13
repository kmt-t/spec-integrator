from __future__ import annotations

import re
from pathlib import Path

from spec_integrator.source.models import SourceIssue

_WORK_MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b\s*[:：]?")


class CppRuleAnalyzer:
    """Applies configured lexical rules to C and C++ source files."""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir

    def check_gate_rules(
        self,
        file_path: Path,
        content: str,
        rel_path: str,
        rules: list[str],
        group_name: str,
    ) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        if not rules or "todo_comment" in rules:
            for index, line in enumerate(content.splitlines(), start=1):
                match = _WORK_MARKER_RE.search(line)
                if match:
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=index,
                            rule="SABOTAGE-TODO-COMMENT",
                            severity="WARNING",
                            message=f"Unresolved work marker found: '{match.group(0)}' in source code.",
                            group=group_name,
                        )
                    )

        if file_path.suffix.lower() in (".hxx", ".cxx", ".c", ".h", ".cpp"):
            if "empty_function" in rules:
                for index, line in enumerate(content.splitlines(), start=1):
                    if re.search(r"\)\s*(?:const)?\s*\{\s*\}", line):
                        if not re.search(r"(?:~?[A-Za-z0-9_]+\s*\(\s*\)\s*\{\s*\})", line):
                            issues.append(
                                SourceIssue(
                                    file_path=rel_path,
                                    line=index,
                                    rule="SABOTAGE-EMPTY-FUNCTION",
                                    severity="WARNING",
                                    message="Empty function body '{}' detected.",
                                    group=group_name,
                                )
                            )
        return issues

    def check_cpp_rules(
        self, file_path: Path, rules: list[str], group_name: str
    ) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return issues

        rel_path = (
            str(file_path.relative_to(self.root_dir)).replace("\\", "/")
            if file_path.is_relative_to(self.root_dir)
            else str(file_path)
        )
        lines = content.splitlines()

        # Skip platform allocator implementations if they define malloc/free internally
        is_allocator_impl = "allocator" in rel_path

        for idx, line_str in enumerate(lines, start=1):
            # Strip comments
            code_part = line_str.split("//")[0].strip()

            if "forbidden_malloc" in rules and not is_allocator_impl:
                if re.search(r"\b(malloc|free|calloc|realloc)\s*\(", code_part):
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=idx,
                            rule="CPP-FORBIDDEN-MALLOC",
                            severity="ERROR",
                            message="Dynamic memory allocation (malloc/free) is forbidden in embedded core. Use static or stack allocation.",
                            group=group_name,
                        )
                    )

            if "forbidden_new" in rules and not is_allocator_impl:
                # Disallow 'new Type' (allow placement new '#include <new>' or 'new (ptr)')
                if re.search(r"\bnew\s+[A-Za-z0-9_]+(?:\s*\(|\s*\[|\s*\{)", code_part):
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=idx,
                            rule="CPP-FORBIDDEN-NEW",
                            severity="ERROR",
                            message="Dynamic operator 'new' is forbidden in Fireball core.",
                            group=group_name,
                        )
                    )

            if "forbidden_exceptions" in rules:
                if re.search(r"\b(throw\s+|catch\s*\(|try\s*\{)", code_part):
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=idx,
                            rule="CPP-FORBIDDEN-EXCEPTIONS",
                            severity="ERROR",
                            message="C++ exceptions (throw/try/catch) are forbidden. Return explicit error codes/enums.",
                            group=group_name,
                        )
                    )

            if "forbidden_rtti" in rules:
                if re.search(r"\b(typeid\s*\(|dynamic_cast\s*<)", code_part):
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=idx,
                            rule="CPP-FORBIDDEN-RTTI",
                            severity="ERROR",
                            message="C++ RTTI (typeid/dynamic_cast) is forbidden.",
                            group=group_name,
                        )
                    )

        # Namespace fireball check for public headers in inc/
        if "fireball_namespace" in rules and rel_path.startswith("inc/"):
            if "namespace fireball" not in content:
                issues.append(
                    SourceIssue(
                        file_path=rel_path,
                        line=1,
                        rule="CPP-MISSING-FIREBALL-NAMESPACE",
                        severity="ERROR",
                        message="Public header under 'inc/' must declare 'namespace fireball'.",
                        group=group_name,
                    )
                )

        return issues
