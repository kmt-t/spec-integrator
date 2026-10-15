from __future__ import annotations

import ast
import fnmatch
import io
import re
import tokenize
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spec_integrator.config import Config

from spec_integrator.source.models import SourceIssue

_WORK_MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b\s*[:：]?")
_TYPING_MODULES = frozenset(("typing", "typing_extensions"))


class PythonRuleAnalyzer:
    """Applies the configured static rules to Python ASTs and comments."""

    def __init__(self, config: Config):
        self.config = config

    def _check_python_rules(
        self,
        file_path: Path,
        content: str,
        rel_path: str,
        rules: list[str],
        group_name: str,
        rtti_exclude_paths: list[str] | None = None,
        raise_exclude_paths: list[str] | None = None,
        test_backdoor_exclude_paths: list[str] | None = None,
        forbidden_product_symbols: list[str] | None = None,
        non_none_union_exclude_paths: list[str] | None = None,
        string_member_exclude_paths: list[str] | None = None,
        object_type_exclude_paths: list[str] | None = None,
        none_default_fill_exclude_paths: list[str] | None = None,
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
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (object_type_exclude_paths or [])
            )
            if not excluded:
                issues.extend(self._check_python_object_type(tree, rel_path, group_name))
        if "forbid_assert_side_effects" in rules:
            issues.extend(self._check_assert_side_effects(tree, rel_path, group_name))
        if "forbid_non_none_union" in rules:
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (non_none_union_exclude_paths or [])
            )
            if not excluded:
                issues.extend(self._check_python_non_none_unions(tree, rel_path, group_name))
        if "forbid_none_default_fill" in rules:
            normalized_path = rel_path.replace("\\", "/").lstrip("./")
            excluded = any(
                fnmatch.fnmatch(normalized_path, pattern.replace("\\", "/").lstrip("./"))
                for pattern in (none_default_fill_exclude_paths or [])
            )
            if not excluded:
                issues.extend(self._check_python_none_default_fill(tree, rel_path, group_name))
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

    @staticmethod
    def _check_assert_side_effects(
        tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        """Detect known state-changing calls even in multiline assert expressions."""
        mutators = frozenset(
            (
                "append",
                "extend",
                "insert",
                "pop",
                "pop_back",
                "pop_front",
                "pop_label",
                "set_size",
                "truncate",
                "reverse_in_place",
                "put",
                "enqueue",
                "enqueue_front",
                "dequeue",
                "clear",
                "remove",
                "erase",
                "grant_shared",
                "revoke_shared",
                "update_owner",
                "map_for_io",
                "_write_guest",
                "_PYOBJECT_GET_BUFFER",
            )
        )
        # Only parse strings passed as executable Python (-c), not documentation.
        scripts: dict[int, ast.Constant] = {}
        assignments: dict[str, list[ast.Constant]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            assignments.setdefault(target.id, []).append(node.value)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr not in (
                "run",
                "Popen",
                "call",
                "check_call",
                "check_output",
            ):
                continue
            command = node.args[0]
            if not isinstance(command, (ast.List, ast.Tuple)):
                continue
            for index, arg in enumerate(command.elts[:-1]):
                if not isinstance(arg, ast.Constant) or arg.value != "-c":
                    continue
                script = command.elts[index + 1]
                constants = (
                    assignments.get(script.id, [])
                    if isinstance(script, ast.Name)
                    else (
                        [script]
                        if isinstance(script, ast.Constant) and isinstance(script.value, str)
                        else []
                    )
                )
                for constant in constants:
                    scripts[id(constant)] = constant
        trees = [tree]
        for script in scripts.values():
            assert isinstance(script.value, str)
            try:
                embedded = ast.parse(script.value)
            except SyntaxError:
                continue
            ast.increment_lineno(embedded, script.lineno - 1)
            trees.append(embedded)
        issues: list[SourceIssue] = []
        for assertion in (node for source in trees for node in ast.walk(source)):
            if not isinstance(assertion, ast.Assert):
                continue
            for node in ast.walk(assertion.test):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                if name not in mutators and not name.startswith(("push_", "pop_")):
                    continue
                issues.append(
                    SourceIssue(
                        file_path=rel_path,
                        line=node.lineno,
                        rule="PY-ASSERT-SIDE-EFFECT",
                        severity="ERROR",
                        message=f"State-changing {name}() must execute before assert; assert is removed by -O.",
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

    @staticmethod
    def _none_comparison(node: ast.AST) -> tuple[str, bool] | None:
        """Return the compared name and whether the condition is true for None."""
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
            return None

        left = node.left
        right = node.comparators[0]
        if isinstance(left, ast.Name) and PythonRuleAnalyzer._is_none_type(right):
            variable = left
        elif isinstance(right, ast.Name) and PythonRuleAnalyzer._is_none_type(left):
            variable = right
        else:
            return None

        operator = node.ops[0]
        if isinstance(operator, ast.Is):
            return variable.id, True
        if isinstance(operator, ast.IsNot):
            return variable.id, False
        return None

    @staticmethod
    def _is_direct_assignment_to(statement: ast.stmt, name: str) -> bool:
        if isinstance(statement, ast.Assign):
            return any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            )
        return isinstance(statement, ast.AnnAssign) and (
            isinstance(statement.target, ast.Name) and statement.target.id == name
        )

    def _none_defaulted_argument_names(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> set[str]:
        """Return arguments whose declared default is explicitly None."""
        arguments = node.args.posonlyargs + node.args.args
        default_none_names: set[str] = set()
        positional_defaults = node.args.defaults
        if positional_defaults:
            positional_with_defaults = arguments[-len(positional_defaults) :]
            default_none_names.update(
                argument.arg
                for argument, default in zip(
                    positional_with_defaults, positional_defaults, strict=True
                )
                if self._is_none_type(default)
            )
        default_none_names.update(
            argument.arg
            for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
            if default is not None and self._is_none_type(default)
        )
        return default_none_names

    def _check_python_none_default_fill(
        self, tree: ast.Module, rel_path: str, group_name: str
    ) -> list[SourceIssue]:
        """Reject selecting a default in place of an optional value."""
        issues: list[SourceIssue] = []
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        default_none_names_by_function = {
            node: self._none_defaulted_argument_names(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.IfExp):
                comparison = self._none_comparison(node.test)
                if comparison is None:
                    continue
                name, condition_is_none = comparison
                scope = parents.get(node)
                while scope is not None and not isinstance(
                    scope, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    scope = parents.get(scope)
                if scope is not None and name in default_none_names_by_function.get(scope, set()):
                    continue
                present_value = node.orelse if condition_is_none else node.body
                default_value = node.body if condition_is_none else node.orelse
                if (
                    isinstance(present_value, ast.Name)
                    and present_value.id == name
                    and not (isinstance(default_value, ast.Name) and default_value.id == name)
                    and not self._is_none_type(default_value)
                ):
                    issues.append(
                        SourceIssue(
                            file_path=rel_path,
                            line=getattr(node, "lineno", 1),
                            rule="PY-FORBIDDEN-NONE-DEFAULT-FILL",
                            severity="ERROR",
                            message="None must not be replaced with a default; resolve required values at the caller.",
                            group=group_name,
                        )
                    )
                continue

            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            default_none_names = self._none_defaulted_argument_names(node)
            if not default_none_names or not node.body:
                continue

            first_statement = node.body[0]
            if (
                isinstance(first_statement, ast.Expr)
                and isinstance(first_statement.value, ast.Constant)
                and isinstance(first_statement.value.value, str)
            ):
                if len(node.body) < 2:
                    continue
                first_statement = node.body[1]
            if not isinstance(first_statement, ast.If):
                continue

            comparison = self._none_comparison(first_statement.test)
            if comparison is None:
                continue
            name, condition_is_none = comparison
            if name not in default_none_names:
                continue
            none_branch = first_statement.body if condition_is_none else first_statement.orelse
            if any(
                self._is_direct_assignment_to(statement, name)
                and not (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.value, ast.Name)
                    and statement.value.id == name
                )
                and not (isinstance(statement, ast.Assign) and self._is_none_type(statement.value))
                and not (
                    isinstance(statement, ast.AnnAssign) and self._is_none_type(statement.value)
                )
                for statement in none_branch
            ):
                issues.append(
                    SourceIssue(
                        file_path=rel_path,
                        line=getattr(first_statement, "lineno", 1),
                        rule="PY-FORBIDDEN-NONE-DEFAULT-FILL",
                        severity="ERROR",
                        message="An argument defaulted to None must not be replaced at function entry.",
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
            positional_defaults = [None] * (
                len(node.args.posonlyargs) + len(node.args.args) - len(node.args.defaults)
            )
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
                ) or (isinstance(default, ast.Constant) and default.value is None)
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
                if isinstance(statement.value, ast.Constant) and isinstance(
                    statement.value.value, str
                ):
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
            return PythonRuleAnalyzer._union_parts(node.left) + PythonRuleAnalyzer._union_parts(
                node.right
            )
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
                annotations.extend(
                    arg.annotation for arg in arguments if arg.annotation is not None
                )
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
                    value_name = (
                        node.value.id if isinstance(node.value, ast.Name) else node.value.attr
                    )
                    if value_name == "Union":
                        union_parts = (
                            list(node.slice.elts)
                            if isinstance(node.slice, ast.Tuple)
                            else [node.slice]
                        )
                    elif value_name == "Optional":
                        optional_parts = (
                            list(node.slice.elts)
                            if isinstance(node.slice, ast.Tuple)
                            else [node.slice]
                        )
                        if len(optional_parts) != 1:
                            union_parts = optional_parts
                if union_parts is None:
                    continue
                if (
                    len(union_parts) != 2
                    or sum(self._is_none_type(part) for part in union_parts) != 1
                ):
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
        typing_modules = {"typing", "typing_extensions"}
        protocol_names: set[str] = set()
        overload_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                typing_modules.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in {"typing", "typing_extensions"}
                )
            elif isinstance(node, ast.ImportFrom) and node.module in {
                "typing",
                "typing_extensions",
            }:
                for alias in node.names:
                    if alias.name == "Protocol":
                        protocol_names.add(alias.asname or alias.name)
                    elif alias.name == "overload":
                        overload_names.add(alias.asname or alias.name)

        def typing_symbol(node: ast.expr, names: set[str], attribute: str) -> bool:
            return (isinstance(node, ast.Name) and node.id in names) or (
                isinstance(node, ast.Attribute)
                and node.attr == attribute
                and isinstance(node.value, ast.Name)
                and node.value.id in typing_modules
            )

        def is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
            return any(
                typing_symbol(item, overload_names, "overload") for item in node.decorator_list
            )

        def is_placeholder(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
            body = node.body
            return len(body) == 1 and (
                isinstance(body[0], ast.Pass)
                or (
                    isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and body[0].value.value is Ellipsis
                )
            )

        contract_methods: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                typing_symbol(
                    base.value if isinstance(base, ast.Subscript) else base,
                    protocol_names,
                    "Protocol",
                )
                for base in node.bases
            ):
                contract_methods.update(
                    id(item)
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
            # Overload signatures are contracts only when a concrete implementation
            # exists in the same lexical body. An orphan overload remains an error.
            for body in (value for _, value in ast.iter_fields(node) if isinstance(value, list)):
                functions = [
                    item
                    for item in body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                implemented = {
                    item.name
                    for item in functions
                    if not is_overload(item) and not is_placeholder(item)
                }
                contract_methods.update(
                    id(item) for item in functions if is_overload(item) and item.name in implemented
                )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or len(node.body) != 1:
                continue
            if id(node) in contract_methods:
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
