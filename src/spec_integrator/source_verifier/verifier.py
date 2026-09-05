from __future__ import annotations

import ast
import fnmatch
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spec_integrator.config import Config, SourceGroupConfig


@dataclass
class SourceIssue:
    file_path: str
    line: int
    rule: str
    severity: str  # "ERROR" or "WARNING"
    message: str
    group: str = ""


@dataclass
class SourceVerificationResult:
    group: str
    files_evaluated: int = 0
    issues: list[SourceIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "FAIL" if any(i.severity == "ERROR" for i in self.issues) else "PASS"


class SourceVerifier:
    """Verifies source code according to project rules, anti-sabotage checks, and group configuration."""

    def __init__(self, config: Config):
        self.config = config
        self.root_dir = config.config_dir

    def resolve_group_names(self, group_filter: str | None = None) -> list[str]:
        all_groups = self.config.source_verification.groups
        if not group_filter or group_filter.lower() in ("all", "*"):
            return list(all_groups.keys())

        gf = group_filter.lower()
        if gf == "python":
            return [g for g in all_groups if g.startswith("python_")]
        if gf in ("concepts", "concept"):
            return [g for g in all_groups if "concept" in g]
        if gf in ("formal", "model"):
            return [g for g in all_groups if "formal" in g]
        if gf in ("pysim", "sim"):
            return [g for g in all_groups if "pysim" in g]
        if gf in all_groups:
            return [gf]
        # Match prefix or substring
        matches = [g for g in all_groups if gf in g]
        if matches:
            return matches
        return []

    def collect_files_for_group(
        self,
        group_name: str,
        explicit_files: list[str | Path] | None = None,
    ) -> list[Path]:
        group_cfg = self.config.source_verification.groups.get(group_name)
        if not group_cfg:
            return []

        if explicit_files:
            matched: list[Path] = []
            for f in explicit_files:
                p = Path(f).resolve()
                if not p.is_file():
                    continue
                # Check extension
                ext = p.suffix.lower()
                matches_ext = not group_cfg.extensions or ext in [
                    e.lower() for e in group_cfg.extensions
                ]
                # Check patterns
                matches_pat = not group_cfg.patterns or any(
                    fnmatch.fnmatch(p.name, pat) for pat in group_cfg.patterns
                )
                if matches_ext and matches_pat:
                    matched.append(p)
            return sorted(list(set(matched)))

        # Auto-discover from include_dirs
        collected: list[Path] = []
        for idir in group_cfg.include_dirs:
            dir_path = self.root_dir / idir
            if not dir_path.exists():
                continue
            for p in dir_path.rglob("*"):
                if not p.is_file():
                    continue
                ext = p.suffix.lower()
                matches_ext = not group_cfg.extensions or ext in [
                    e.lower() for e in group_cfg.extensions
                ]
                matches_pat = not group_cfg.patterns or any(
                    fnmatch.fnmatch(p.name, pat) for pat in group_cfg.patterns
                )
                if matches_ext and matches_pat:
                    collected.append(p.resolve())

        return sorted(list(set(collected)))

    def verify_group(
        self,
        group_name: str,
        files: list[Path],
    ) -> SourceVerificationResult:
        group_cfg = self.config.source_verification.groups.get(group_name)
        result = SourceVerificationResult(group=group_name, files_evaluated=len(files))
        if not group_cfg or not files:
            return result

        for check_rule in group_cfg.checks:
            if not check_rule.enabled:
                continue

            cid = check_rule.id
            if cid == "anti_sabotage":
                for f in files:
                    result.issues.extend(
                        self._check_anti_sabotage(f, check_rule.rules, group_name)
                    )
            elif cid == "cpp_rules":
                for f in files:
                    result.issues.extend(self._check_cpp_rules(f, check_rule.rules, group_name))
            elif cid == "ruff":
                py_files = [f for f in files if f.suffix.lower() == ".py"]
                if py_files:
                    result.issues.extend(self._run_ruff(py_files, group_name))
            elif cid in ("execute", "execute_formal"):
                for f in files:
                    if f.suffix.lower() == ".py":
                        result.issues.extend(self._execute_python_file(f, group_name))
            elif cid == "run_tests":
                # For pysim, run scenarios/unit tests
                result.issues.extend(self._run_pysim_tests(group_name))

        return result

    def _check_anti_sabotage(
        self, file_path: Path, rules: list[str], group_name: str
    ) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return [
                SourceIssue(
                    file_path=str(file_path.relative_to(self.root_dir)),
                    line=1,
                    rule="SABOTAGE-READ-ERROR",
                    severity="ERROR",
                    message=f"Failed to read file: {e}",
                    group=group_name,
                )
            ]

        rel_path = (
            str(file_path.relative_to(self.root_dir)).replace("\\", "/")
            if file_path.is_relative_to(self.root_dir)
            else str(file_path)
        )
        lines = content.splitlines()

        # 1. TODO / FIXME / HACK comments check
        if not rules or "todo_comment" in rules:
            for idx, line_str in enumerate(lines, start=1):
                # Ignore if inside a docstring or test fixture name
                m = re.search(r"\b(TODO|FIXME|XXX|HACK)\b\s*[:：]?", line_str)
                if m:
                    # Ignore comment markers in markdown or documentation files
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=idx,
                            rule="SABOTAGE-TODO-COMMENT",
                            severity="WARNING",
                            message=f"Unresolved work marker found: '{m.group(0)}' in source code.",
                            group=group_name,
                        )
                    )

        # 2. Python specific anti-sabotage (typing.Any prohibition & dummy functions)
        if file_path.suffix.lower() == ".py":
            # forbid_typing_any
            if not rules or "forbid_typing_any" in rules:
                for idx, line_str in enumerate(lines, start=1):
                    # Check "from typing import ... Any ..." or "import typing" + "typing.Any"
                    if re.search(r"from\s+typing\s+import\b[^#]*\bAny\b", line_str) or re.search(
                        r"\btyping\.Any\b", line_str
                    ):
                        issues.append(
                            SourceIssue(
                                file_path=rel_path,
                                line=idx,
                                rule="PY-FORBIDDEN-TYPING-ANY",
                                severity="ERROR",
                                message="Use of 'typing.Any' is strictly forbidden in Fireball. Use specific types or algebraic data types.",
                                group=group_name,
                            )
                        )

            # dummy_pass / empty function body check via AST
            if not rules or "dummy_pass" in rules or "empty_function" in rules:
                try:
                    tree = ast.parse(content, filename=str(file_path))
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            # Check if body is only pass, Ellipsis, or return constant
                            if len(node.body) == 1:
                                single = node.body[0]
                                if isinstance(single, ast.Pass):
                                    issues.append(
                                        SourceIssue(
                                            file_path=rel_path,
                                            line=node.lineno,
                                            rule="SABOTAGE-EMPTY-FUNCTION",
                                            severity="ERROR",
                                            message=f"Function '{node.name}' has empty 'pass' implementation (dummy placeholder).",
                                            group=group_name,
                                        )
                                    )
                                elif (
                                    isinstance(single, ast.Expr)
                                    and isinstance(single.value, ast.Constant)
                                    and single.value.value is Ellipsis
                                ):
                                    issues.append(
                                        SourceIssue(
                                            file_path=rel_path,
                                            line=node.lineno,
                                            rule="SABOTAGE-EMPTY-FUNCTION",
                                            severity="ERROR",
                                            message=f"Function '{node.name}' has empty '...' implementation.",
                                            group=group_name,
                                        )
                                    )
                except SyntaxError:
                    pass

            # formal mutation guards check
            if "mutation_guards_required" in rules:
                if "guards=False" not in content and "guards = False" not in content:
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=1,
                            rule="FORMAL-MUTATION-GUARD-MISSING",
                            severity="ERROR",
                            message="Formal model must include 'guards=False' mutation verification test.",
                            group=group_name,
                        )
                    )

        # 3. C++ specific empty function check
        if file_path.suffix.lower() in (".hxx", ".cxx", ".c", ".h", ".cpp"):
            if "empty_function" in rules:
                for idx, line_str in enumerate(lines, start=1):
                    # Check for empty function implementation `{}` on the same line
                    if re.search(r"\)\s*(?:const)?\s*\{\s*\}", line_str):
                        # Allow default constructor/destructor
                        if not re.search(r"(?:~?[A-Za-z0-9_]+\s*\(\s*\)\s*\{\s*\})", line_str):
                            issues.append(
                                SourceIssue(
                                    file_path=rel_path,
                                    line=idx,
                                    rule="SABOTAGE-EMPTY-FUNCTION",
                                    severity="WARNING",
                                    message="Empty function body '{}' detected.",
                                    group=group_name,
                                )
                            )

        return issues

    def _check_cpp_rules(
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

    def _run_ruff(self, files: list[Path], group_name: str) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        ruff_bin = shutil.which("ruff")
        base_cmd = (
            [ruff_bin] if ruff_bin else ["uv", "run", "--system-certs", "--with", "ruff", "ruff"]
        )

        # Run ruff check on the list of files
        file_args = [str(f) for f in files]
        res = subprocess.run(
            [*base_cmd, "check", *file_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode != 0:
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("Found "):
                    continue
                issues.append(
                    SourceIssue(
                        file_path="[python]",
                        line=1,
                        rule="PY-RUFF-LINT",
                        severity="ERROR",
                        message=line,
                        group=group_name,
                    )
                )
        return issues

    def _execute_python_file(self, file_path: Path, group_name: str) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        rel_path = (
            str(file_path.relative_to(self.root_dir)).replace("\\", "/")
            if file_path.is_relative_to(self.root_dir)
            else str(file_path)
        )
        # Run with uv
        cmd = [
            "uv",
            "run",
            "--system-certs",
            "--project",
            "tools/spec-integrator",
            "python",
            str(file_path),
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if res.returncode != 0:
                err_msg = res.stderr.strip() or res.stdout.strip()
                last_line = err_msg.splitlines()[-1] if err_msg else "Exit code non-zero"
                issues.append(
                    SourceIssue(
                        file_path=rel_path,
                        line=1,
                        rule="PY-EXECUTION-FAILED",
                        severity="ERROR",
                        message=f"Execution failed: {last_line}",
                        group=group_name,
                    )
                )
        except subprocess.TimeoutExpired:
            issues.append(
                SourceIssue(
                    file_path=rel_path,
                    line=1,
                    rule="PY-EXECUTION-TIMEOUT",
                    severity="ERROR",
                    message="Execution timed out after 30 seconds.",
                    group=group_name,
                )
            )
        except Exception as e:
            issues.append(
                SourceIssue(
                    file_path=rel_path,
                    line=1,
                    rule="PY-EXECUTION-ERROR",
                    severity="ERROR",
                    message=f"Failed to execute: {e}",
                    group=group_name,
                )
            )
        return issues

    def _run_pysim_tests(self, group_name: str) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        test_runners = [
            self.root_dir / "experiments/pysim/tests/run_all.py",
            self.root_dir / "experiments/pysim/scenarios/run_all.py",
        ]
        for tr in test_runners:
            if not tr.exists():
                continue
            rel = str(tr.relative_to(self.root_dir)).replace("\\", "/")
            cmd = [
                "uv",
                "run",
                "--system-certs",
                "--project",
                "tools/spec-integrator",
                "--with",
                "wasmtime",
                "python",
                str(tr),
            ]
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                if res.returncode != 0:
                    err_msg = res.stderr.strip() or res.stdout.strip()
                    last_line = err_msg.splitlines()[-1] if err_msg else "Exit code non-zero"
                    issues.append(
                        SourceIssue(
                            file_path=rel,
                            line=1,
                            rule="PYSIM-TEST-FAILED",
                            severity="ERROR",
                            message=f"Pysim test suite failed: {last_line}",
                            group=group_name,
                        )
                    )
            except Exception as e:
                issues.append(
                    SourceIssue(
                        file_path=rel,
                        line=1,
                        rule="PYSIM-TEST-ERROR",
                        severity="ERROR",
                        message=f"Failed to run pysim tests: {e}",
                        group=group_name,
                    )
                )
        return issues
