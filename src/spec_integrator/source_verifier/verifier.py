from __future__ import annotations

import ast
import fnmatch
import io
import re
import shutil
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spec_integrator.config import Config


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


_WORK_MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b\s*[:：]?")
_TYPING_MODULES = frozenset(("typing", "typing_extensions"))


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
            return sorted(set(matched))

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

        return sorted(set(collected))

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
                        self._check_anti_sabotage(
                            f,
                            check_rule.rules,
                            group_name,
                            check_rule.builtin_container_exclude_paths,
                            check_rule.rtti_exclude_paths,
                            check_rule.in_operator_exclude_paths,
                            check_rule.raise_exclude_paths,
                            check_rule.test_backdoor_exclude_paths,
                            check_rule.forbidden_product_symbols,
                            check_rule.non_none_union_exclude_paths,
                            check_rule.string_member_exclude_paths,
                        )
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
                # Pysim source verification runs the unit-test suite only.
                # Scenarios and benchmarks are review fixtures, not source-gate inputs.
                result.issues.extend(self._run_pysim_tests(group_name))
            elif cid == "pysim_imports":
                result.issues.extend(self._check_pysim_imports(group_name))

        return result

    def _check_anti_sabotage(
        self,
        file_path: Path,
        rules: list[str],
        group_name: str,
        builtin_container_exclude_paths: list[str] | None = None,
        rtti_exclude_paths: list[str] | None = None,
        in_operator_exclude_paths: list[str] | None = None,
        raise_exclude_paths: list[str] | None = None,
        test_backdoor_exclude_paths: list[str] | None = None,
        forbidden_product_symbols: list[str] | None = None,
        non_none_union_exclude_paths: list[str] | None = None,
        string_member_exclude_paths: list[str] | None = None,
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
        is_python = file_path.suffix.lower() == ".py"
        if is_python:
            issues.extend(
                self._check_python_rules(
                    file_path,
                    content,
                    rel_path,
                    rules,
                    group_name,
                    builtin_container_exclude_paths or [],
                    rtti_exclude_paths or [],
                    in_operator_exclude_paths or [],
                    raise_exclude_paths or [],
                    test_backdoor_exclude_paths or [],
                    forbidden_product_symbols or [],
                    non_none_union_exclude_paths or [],
                    string_member_exclude_paths or [],
                )
            )
        else:
            # C++ is dispatched to its language-specific analyzer. The current
            # rules remain the compatibility path until the clang AST backend
            # is wired into this same boundary.
            if not rules or "todo_comment" in rules:
                for idx, line_str in enumerate(content.splitlines(), start=1):
                    match = _WORK_MARKER_RE.search(line_str)
                    if match:
                        issues.append(
                            SourceIssue(
                                file_path=rel_path,
                                line=idx,
                                rule="SABOTAGE-TODO-COMMENT",
                                severity="WARNING",
                                message=f"Unresolved work marker found: '{match.group(0)}' in source code.",
                                group=group_name,
                            )
                        )

        # 3. C++ specific empty function check
        if file_path.suffix.lower() in (".hxx", ".cxx", ".c", ".h", ".cpp"):
            if "empty_function" in rules:
                for idx, line_str in enumerate(content.splitlines(), start=1):
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

    def _check_pysim_imports(self, group_name: str) -> list[SourceIssue]:
        """Checks configured pysim product imports against Tier direction."""
        cfg = self.config.pysim_imports
        if not cfg.enabled:
            return []

        root = self.config.resolve_path(cfg.root)
        if not cfg.tiers:
            return [
                SourceIssue(
                    file_path=str(self.config.config_dir / "spec-integrator.yaml"),
                    line=1,
                    rule="PYSIM-IMPORT-CONFIG",
                    severity="ERROR",
                    message="pysim_imports.tiers must define product source file Tier assignments.",
                    group=group_name,
                )
            ]

        file_tiers: dict[Path, list[int]] = {}
        for tier_cfg in cfg.tiers:
            for pattern in tier_cfg.paths:
                for file_path in root.glob(pattern):
                    relative = file_path.relative_to(root).as_posix()
                    excluded = any(fnmatch.fnmatch(relative, item) for item in tier_cfg.exclude)
                    if file_path.is_file() and file_path.suffix == ".py" and not excluded:
                        file_tiers.setdefault(file_path.resolve(), []).append(tier_cfg.tier)

        issues: list[SourceIssue] = []
        for file_path, tiers in file_tiers.items():
            if len(set(tiers)) != 1:
                issues.append(
                    SourceIssue(
                        file_path=self._relative_source_path(file_path),
                        line=1,
                        rule="PYSIM-IMPORT-CONFIG",
                        severity="ERROR",
                        message=f"Product source file has multiple configured Tiers: {sorted(set(tiers))}.",
                        group=group_name,
                    )
                )

        module_tiers: dict[str, set[int]] = {}
        for file_path, tiers in file_tiers.items():
            if len(set(tiers)) != 1:
                continue
            tier = tiers[0]
            relative = file_path.relative_to(root).with_suffix("")
            parts = relative.parts
            module_tiers.setdefault(parts[-1], set()).add(tier)
            for index in range(1, len(parts) + 1):
                module_tiers.setdefault(".".join(parts[:index]), set()).add(tier)

        for file_path, tiers in file_tiers.items():
            if len(set(tiers)) != 1:
                continue
            source_tier = tiers[0]
            try:
                tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
            except SyntaxError as error:
                issues.append(
                    SourceIssue(
                        file_path=self._relative_source_path(file_path),
                        line=error.lineno or 1,
                        rule="PYSIM-IMPORT-SYNTAX",
                        severity="ERROR",
                        message=f"Cannot inspect imports because the file has a syntax error: {error.msg}",
                        group=group_name,
                    )
                )
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [(alias.name, node.lineno) for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported = [(node.module, node.lineno)]
                else:
                    continue

                for module, line in imported:
                    imported_tiers = module_tiers.get(module)
                    if not imported_tiers:
                        continue
                    if len(imported_tiers) != 1:
                        issues.append(
                            SourceIssue(
                                file_path=self._relative_source_path(file_path),
                                line=line,
                                rule="PYSIM-IMPORT-CONFIG",
                                severity="ERROR",
                                message=f"Imported module '{module}' has multiple configured Tiers: {sorted(imported_tiers)}.",
                                group=group_name,
                            )
                        )
                        continue
                    imported_tier = next(iter(imported_tiers))
                    if source_tier < imported_tier:
                        issues.append(
                            SourceIssue(
                                file_path=self._relative_source_path(file_path),
                                line=line,
                                rule="PYSIM-IMPORT-DIRECTION",
                                severity="ERROR",
                                message=(
                                    f"Configured Tier {source_tier} product code must not import "
                                    f"'{module}' from Tier {imported_tier}."
                                ),
                                group=group_name,
                            )
                        )
        return issues

    def _relative_source_path(self, file_path: Path) -> str:
        """Returns a stable repository-relative path for source issues."""
        return str(file_path.relative_to(self.root_dir)).replace("\\", "/")

    def _check_python_rules(
        self,
        file_path: Path,
        content: str,
        rel_path: str,
        rules: list[str],
        group_name: str,
        builtin_container_exclude_paths: list[str] | None = None,
        rtti_exclude_paths: list[str] | None = None,
        in_operator_exclude_paths: list[str] | None = None,
        raise_exclude_paths: list[str] | None = None,
        test_backdoor_exclude_paths: list[str] | None = None,
        forbidden_product_symbols: list[str] | None = None,
        non_none_union_exclude_paths: list[str] | None = None,
        string_member_exclude_paths: list[str] | None = None,
    ) -> list[SourceIssue]:
        """Checks Python source from one parsed AST, with comments read by tokenize."""
        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as error:
            return [
                SourceIssue(
                    file_path=rel_path,
                    line=error.lineno or 1,
                    rule="PY-SYNTAX-ERROR",
                    severity="ERROR",
                    message=f"Python AST parsing failed: {error.msg}",
                    group=group_name,
                )
            ]

        issues: list[SourceIssue] = []
        if not rules or "todo_comment" in rules:
            issues.extend(self._check_python_work_markers(content, rel_path, group_name))
        if not rules or "forbid_typing_any" in rules:
            issues.extend(self._check_python_typing_any(tree, rel_path, group_name))
        if "forbid_object_type" in rules:
            issues.extend(self._check_python_object_type(tree, rel_path, group_name))
        if "forbid_non_none_union" in rules:
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (non_none_union_exclude_paths or [])
            )
            if not excluded:
                issues.extend(self._check_python_non_none_unions(tree, rel_path, group_name))
        if "forbid_string_members" in rules:
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (string_member_exclude_paths or [])
            )
            if not excluded:
                issues.extend(self._check_python_string_members(tree, rel_path, group_name))
        if "forbid_raise" in rules:
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (raise_exclude_paths or [])
            )
            if not excluded:
                issues.extend(self._check_python_raise(tree, rel_path, group_name))
        if "forbid_rtti" in rules:
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (rtti_exclude_paths or [])
            )
            if not excluded:
                issues.extend(self._check_python_rtti(tree, rel_path, group_name))
        if "forbid_in_operator" in rules:
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (in_operator_exclude_paths or [])
            )
            if not excluded:
                issues.extend(self._check_python_in_operator(tree, rel_path, group_name))
        if "forbid_test_backdoor" in rules:
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (test_backdoor_exclude_paths or [])
            )
            if not excluded:
                issues.extend(
                    self._check_python_test_backdoor(
                        tree, rel_path, group_name, forbidden_product_symbols or []
                    )
                )
        if "forbid_required_nullable_arguments" in rules:
            issues.extend(
                self._check_python_required_nullable_arguments(tree, rel_path, group_name)
            )
        if "forbid_builtin_containers" in rules:
            normalized_excludes = {
                path.replace("\\", "/").lstrip("./")
                for path in (builtin_container_exclude_paths or [])
            }
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            if not any(fnmatch.fnmatch(normalized_path, pattern) for pattern in normalized_excludes):
                issues.extend(self._check_python_builtin_containers(tree, rel_path, group_name))
        if not rules or "dummy_pass" in rules or "empty_function" in rules:
            issues.extend(self._check_python_empty_functions(tree, rel_path, group_name))
        if "mutation_guards_required" in rules and not self._has_false_guard_call(tree):
            issues.append(
                SourceIssue(
                    file_path=rel_path,
                    line=1,
                    rule="FORMAL-MUTATION-GUARD-MISSING",
                    severity="ERROR",
                    message="Formal model must include a call with guards=False for mutation verification.",
                    group=group_name,
                )
            )
        return issues

    def _check_python_raise(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        """Reject explicit exception sending while allowing ``try``/``except``."""
        return [
            SourceIssue(
                file_path=rel_path,
                line=getattr(node, "lineno", 1),
                rule="PY-FORBIDDEN-RAISE",
                severity="ERROR",
                message="Explicit 'raise' is forbidden in pysim; return an error result or fail-fast with assert.",
                group=group_name,
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Raise)
        ]

    def _check_python_rtti(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        forbidden = {"isinstance", "type", "hasattr", "getattr", "setattr"}
        issues = [
            SourceIssue(
                file_path=rel_path,
                line=getattr(node, "lineno", 1),
                rule="PY-FORBIDDEN-RTTI",
                severity="ERROR",
                message=f"Dynamic type inspection '{node.func.id}' is forbidden in pysim.",
                group=group_name,
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden
        ]
        forbidden_attributes = {
            "__class__",
            "__dict__",
            "__getattribute__",
            "__getattr__",
            "__setattr__",
            "__instancecheck__",
            "__subclasscheck__",
        }
        issues.extend(
            SourceIssue(
                file_path=rel_path,
                line=getattr(node, "lineno", 1),
                rule="PY-FORBIDDEN-RTTI",
                severity="ERROR",
                message=f"Dynamic type/reflection attribute '{node.attr}' is forbidden in pysim.",
                group=group_name,
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
        )
        return issues

    def _check_python_in_operator(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        return [
            SourceIssue(
                file_path=rel_path,
                line=getattr(node, "lineno", 1),
                rule="PY-FORBIDDEN-IN-OPERATOR",
                severity="ERROR",
                message="The 'in' operator is forbidden in pysim product code; use a bounded view lookup or indexed access.",
                group=group_name,
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(isinstance(operator, (ast.In, ast.NotIn)) for operator in node.ops)
        ]

    def _check_python_test_backdoor(
        self,
        tree: ast.Module,
        rel_path: str,
        group_name: str,
        forbidden_product_symbols: list[str],
    ) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        test_module_names = {"pytest", "unittest", "unittest.mock", "mock"}
        forbidden_symbols = set(forbidden_product_symbols)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in test_module_names:
                        issues.append(
                            SourceIssue(
                                file_path=rel_path,
                                line=getattr(node, "lineno", 1),
                                rule="PY-TEST-CODE-IN-PRODUCT",
                                severity="ERROR",
                                message=f"Test-only module '{alias.name}' is imported by pysim product code.",
                                group=group_name,
                            )
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module in test_module_names:
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=getattr(node, "lineno", 1),
                            rule="PY-TEST-CODE-IN-PRODUCT",
                            severity="ERROR",
                            message=f"Test-only module '{node.module}' is imported by pysim product code.",
                            group=group_name,
                        )
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in forbidden_symbols:
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=getattr(node, "lineno", 1),
                            rule="PY-FORBIDDEN-PRODUCT-SYMBOL",
                            severity="ERROR",
                            message=f"Product symbol '{node.name}' is reserved for tests or obsolete compatibility paths.",
                            group=group_name,
                        )
                    )
                if node.name.startswith("test_") or node.name.startswith("_test_"):
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=getattr(node, "lineno", 1),
                            rule="PY-TEST-CODE-IN-PRODUCT",
                            severity="ERROR",
                            message=f"Test-only symbol '{node.name}' is defined in pysim product code.",
                            group=group_name,
                        )
                    )
        return issues

    def _check_python_required_nullable_arguments(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            positional_defaults = [
                None
            ] * (len(node.args.posonlyargs) + len(node.args.args) - len(node.args.defaults))
            positional_defaults.extend(node.args.defaults)
            defaults = positional_defaults + list(node.args.kw_defaults)
            nullable_arguments: set[str] = set()
            for argument, default in zip(arguments, defaults, strict=True):
                if argument.arg in ("next_pc", "loops_to"):
                    continue
                nullable = (
                    argument.annotation is not None
                    and any(
                        isinstance(part, ast.Constant) and part.value is None
                        for part in ast.walk(argument.annotation)
                    )
                ) or (
                    isinstance(default, ast.Constant) and default.value is None
                )
                if nullable:
                    nullable_arguments.add(argument.arg)

            if node.name.startswith("compile_"):
                for argument in arguments:
                    if argument.arg not in nullable_arguments:
                        continue
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=getattr(argument, "lineno", 1),
                            rule="PY-REQUIRED-ARGUMENT-NULLABLE",
                            severity="ERROR",
                            message=f"Compile API argument '{argument.arg}' must be prepared by the caller; do not accept None as a test or compatibility path.",
                            group=group_name,
                        )
                    )
                continue

            for statement in ast.walk(node):
                if not isinstance(statement, ast.Assert):
                    continue
                if not isinstance(statement.test, ast.Compare):
                    continue
                if len(statement.test.ops) != 1 or not isinstance(statement.test.ops[0], ast.IsNot):
                    continue
                left, right = statement.test.left, statement.test.comparators[0]
                if (
                    isinstance(left, ast.Name)
                    and left.id in nullable_arguments
                    and isinstance(right, ast.Constant)
                    and right.value is None
                ):
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=getattr(statement, "lineno", 1),
                            rule="PY-REQUIRED-ARGUMENT-NULLABLE",
                            severity="ERROR",
                            message=f"Argument '{left.id}' is declared nullable but required by an assertion; make it mandatory and prepare it at the caller.",
                            group=group_name,
                        )
                    )
        return issues

    def _check_python_object_type(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        return [
            SourceIssue(
                file_path=rel_path,
                line=getattr(node, "lineno", 1),
                rule="PY-FORBIDDEN-OBJECT-TYPE",
                severity="ERROR",
                message="Use of 'object' is forbidden in pysim. Use a concrete type or algebraic data type.",
                group=group_name,
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "object"
        ]

    def _check_python_string_members(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        for class_node in ast.walk(tree):
            if not isinstance(class_node, ast.ClassDef):
                continue
            class_constants = [
                statement for statement in class_node.body if isinstance(statement, ast.AnnAssign)
            ]
            instance_members: list[ast.AnnAssign] = []
            for method in class_node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                instance_members.extend(
                    statement
                    for statement in ast.walk(method)
                    if isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Attribute)
                    and isinstance(statement.target.value, ast.Name)
                    and statement.target.value.id in ("self", "cls")
                )
            for statement in class_constants:
                if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                    continue
                if (
                    isinstance(statement.annotation, ast.Subscript)
                    and isinstance(statement.annotation.value, ast.Name)
                    and statement.annotation.value.id in ("ClassVar", "Final")
                ):
                    continue
                if not any(
                    isinstance(item, ast.Name) and item.id == "str"
                    for item in ast.walk(statement.annotation)
                ):
                    continue
                issues.append(
                    SourceIssue(
                        file_path=rel_path,
                        line=getattr(statement, "lineno", 1),
                        rule="PY-FORBIDDEN-STRING-MEMBER",
                        severity="ERROR",
                        message="String-typed class members are forbidden in pysim product code; use a ROM byte range, integer ID, or fixed numeric representation.",
                        group=group_name,
                    )
                )
            for statement in instance_members:
                if not any(
                    isinstance(item, ast.Name) and item.id == "str"
                    for item in ast.walk(statement.annotation)
                ):
                    continue
                issues.append(
                    SourceIssue(
                        file_path=rel_path,
                        line=getattr(statement, "lineno", 1),
                        rule="PY-FORBIDDEN-STRING-MEMBER",
                        severity="ERROR",
                        message="String-typed class members are forbidden in pysim product code; use a ROM byte range, integer ID, or fixed numeric representation.",
                        group=group_name,
                    )
                )
        return issues

    @staticmethod
    def _union_parts(node: ast.AST) -> list[ast.AST]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return SourceVerifier._union_parts(node.left) + SourceVerifier._union_parts(node.right)
        return [node]

    @staticmethod
    def _is_none_type(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value is None

    def _check_python_non_none_unions(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        annotations: list[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign):
                annotations.append(node.annotation)
            elif isinstance(node, ast.Assign):
                # PEP 604 type aliases are ordinary assignments in the AST.
                # A public, capitalized alias such as ``WasiValue = A | B``
                # must therefore be checked as a type annotation as well.
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    target_name = node.targets[0].id
                    if target_name and target_name[0].isupper():
                        annotations.append(node.value)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                annotations.extend(arg.annotation for arg in arguments if arg.annotation is not None)
                if node.args.vararg is not None and node.args.vararg.annotation is not None:
                    annotations.append(node.args.vararg.annotation)
                if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
                    annotations.append(node.args.kwarg.annotation)
                if node.returns is not None:
                    annotations.append(node.returns)

        issues: list[SourceIssue] = []
        for annotation in annotations:
            for node in self._union_nodes(annotation):
                union_parts = self._union_parts(node) if isinstance(node, ast.BinOp) else None
                if isinstance(node, ast.Subscript) and (
                    isinstance(node.value, ast.Name) or isinstance(node.value, ast.Attribute)
                ):
                    value_name = node.value.id if isinstance(node.value, ast.Name) else node.value.attr
                    if value_name == "Union":
                        union_parts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
                    elif value_name == "Optional":
                        optional_parts = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
                        if len(optional_parts) != 1:
                            union_parts = optional_parts
                if union_parts is None:
                    continue
                if len(union_parts) != 2 or sum(self._is_none_type(part) for part in union_parts) != 1:
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=getattr(node, "lineno", 1),
                            rule="PY-FORBIDDEN-NON-NONE-UNION",
                            severity="ERROR",
                            message="Only a nullable union of T | None or Optional[T] is allowed in pysim.",
                            group=group_name,
                        )
                    )
        return issues

    @staticmethod
    def _union_nodes(annotation: ast.AST) -> list[ast.AST]:
        """Returns top-level union expressions without reporting nested PEP 604 nodes twice."""
        nodes: list[ast.AST] = []

        def visit(node: ast.AST) -> None:
            is_pep604_union = isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
            if is_pep604_union:
                nodes.append(node)
                return
            if isinstance(node, ast.Subscript) and (
                isinstance(node.value, ast.Name) or isinstance(node.value, ast.Attribute)
            ):
                value_name = node.value.id if isinstance(node.value, ast.Name) else node.value.attr
                if value_name in ("Union", "Optional"):
                    nodes.append(node)
                    return
            for child in ast.iter_child_nodes(node):
                visit(child)

        visit(annotation)
        return nodes

    def _check_python_builtin_containers(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        """Reject raw dict/set/list syntax in pysim source.

        The checker is intentionally syntax-based: a custom fixed-capacity
        container may use its own implementation details, but product code
        must express ownership and bounds through that container API rather
        than exposing Python's built-in container vocabulary.
        """
        # ``Callable[[Arg1, Arg2], Result]`` uses an AST List as typing
        # syntax, but it does not construct or expose a runtime container.
        callable_parameter_lists = {
            id(node.slice.elts[0])
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            and (
                (isinstance(node.value, ast.Name) and node.value.id == "Callable")
                or (isinstance(node.value, ast.Attribute) and node.value.attr == "Callable")
            )
            and isinstance(node.slice, ast.Tuple)
            and node.slice.elts
            and isinstance(node.slice.elts[0], ast.List)
        }

        issues: list[SourceIssue] = []
        for node in ast.walk(tree):
            rule = ""
            message = ""
            if isinstance(node, ast.List) and id(node) in callable_parameter_lists:
                continue
            if isinstance(node, (ast.List, ast.ListComp)):
                rule = "PY-FORBIDDEN-BUILTIN-LIST"
                message = "Built-in list syntax is forbidden in pysim; use a fixed-capacity system container."
            elif isinstance(node, (ast.Dict, ast.DictComp)):
                rule = "PY-FORBIDDEN-BUILTIN-DICT"
                message = "Built-in dict syntax is forbidden in pysim; use a FlatMap storage or view."
            elif isinstance(node, (ast.Set, ast.SetComp)):
                rule = "PY-FORBIDDEN-BUILTIN-SET"
                message = "Built-in set syntax is forbidden in pysim; use a FlatSet storage or view."
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "list":
                    rule = "PY-FORBIDDEN-BUILTIN-LIST"
                    message = "Calling list() is forbidden in pysim; use a fixed-capacity system container."
                elif node.func.id == "dict":
                    rule = "PY-FORBIDDEN-BUILTIN-DICT"
                    message = "Calling dict() is forbidden in pysim; use a FlatMap storage or view."
                elif node.func.id == "set":
                    rule = "PY-FORBIDDEN-BUILTIN-SET"
                    message = "Calling set() is forbidden in pysim; use a FlatSet storage or view."
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                if node.value.id == "list":
                    rule = "PY-FORBIDDEN-BUILTIN-LIST"
                    message = "list[...] annotations are forbidden in pysim; use a concrete system container type."
                elif node.value.id == "dict":
                    rule = "PY-FORBIDDEN-BUILTIN-DICT"
                    message = "dict[...] annotations are forbidden in pysim; use a concrete system container type."
                elif node.value.id == "set":
                    rule = "PY-FORBIDDEN-BUILTIN-SET"
                    message = "set[...] annotations are forbidden in pysim; use a concrete system container type."
            if rule:
                issues.append(
                    SourceIssue(
                        file_path=rel_path,
                        line=getattr(node, "lineno", 1),
                        rule=rule,
                        severity="ERROR",
                        message=message,
                        group=group_name,
                    )
                )
        return issues

    def _check_python_work_markers(
        self, content: str, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        try:
            tokens = tokenize.generate_tokens(io.StringIO(content).readline)
            for token in tokens:
                if token.type != tokenize.COMMENT:
                    continue
                match = _WORK_MARKER_RE.search(token.string)
                if match:
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=token.start[0],
                            rule="SABOTAGE-TODO-COMMENT",
                            severity="WARNING",
                            message=f"Unresolved work marker found: '{match.group(0)}' in source code.",
                            group=group_name,
                        )
                    )
        except tokenize.TokenError:
            # Syntax errors are reported by the AST parser before this helper runs.
            pass
        return issues

    def _check_python_typing_any(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        typing_aliases = set(_TYPING_MODULES)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _TYPING_MODULES:
                        typing_aliases.add(alias.asname or alias.name)

        any_nodes: list[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in _TYPING_MODULES:
                any_nodes.extend(alias for alias in node.names if alias.name == "Any")
            elif (
                isinstance(node, ast.Attribute)
                and node.attr == "Any"
                and isinstance(node.value, ast.Name)
                and node.value.id in typing_aliases
            ):
                any_nodes.append(node)

        return [
            SourceIssue(
                file_path=rel_path,
                line=getattr(node, "lineno", 1),
                rule="PY-FORBIDDEN-TYPING-ANY",
                severity="ERROR",
                message="Use of 'typing.Any' is strictly forbidden in Fireball. Use specific types or algebraic data types.",
                group=group_name,
            )
            for node in any_nodes
        ]

    def _check_python_empty_functions(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        issues: list[SourceIssue] = []
        protocol_ranges = [
            (node.lineno, node.end_lineno or node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases)
        ]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or len(node.body) != 1:
                continue
            if any(start <= node.lineno <= end for start, end in protocol_ranges):
                # Ellipsis is the standard body for a structural Protocol
                # contract, not an executable placeholder. Keep the empty
                # implementation rule focused on concrete functions.
                continue
            single = node.body[0]
            if isinstance(single, ast.Pass):
                detail = "empty 'pass' implementation (dummy placeholder)"
            elif (
                isinstance(single, ast.Expr)
                and isinstance(single.value, ast.Constant)
                and single.value.value is Ellipsis
            ):
                detail = "empty '...' implementation"
            else:
                continue
            issues.append(
                SourceIssue(
                    file_path=rel_path,
                    line=node.lineno,
                    rule="SABOTAGE-EMPTY-FUNCTION",
                    severity="ERROR",
                    message=f"Function '{node.name}' has {detail}.",
                    group=group_name,
                )
            )
        return issues

    @staticmethod
    def _has_false_guard_call(tree: ast.Module) -> bool:
        return any(
            isinstance(node, ast.Call)
            and any(
                keyword.arg == "guards"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in node.keywords
            )
            for node in ast.walk(tree)
        )

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
        test_runners = (self.root_dir / "experiments/pysim/qa/run_all.py",)
        for tr in test_runners:
            if not tr.exists():
                continue
            rel = str(tr.relative_to(self.root_dir)).replace("\\", "/")
            project_python = self.root_dir / ".venv" / "Scripts" / "python.exe"
            if not project_python.exists():
                project_python = self.root_dir / ".venv" / "bin" / "python"
            test_python = str(project_python) if project_python.exists() else sys.executable
            cmd = [
                # Prefer the repository environment because pysim's tests
                # require project dependencies such as cython and wasmtime.
                # Falling back to spec-integrator's interpreter keeps the
                # checker usable in repositories without a project venv.
                test_python,
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
