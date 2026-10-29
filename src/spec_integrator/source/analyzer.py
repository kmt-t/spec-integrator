from __future__ import annotations

import ast
import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spec_integrator.config import Config

from spec_integrator.source.cpp_rules import CppRuleAnalyzer
from spec_integrator.source.discovery import SourceDiscovery
from spec_integrator.source.execution import SourceExecution
from spec_integrator.source.models import SourceIssue, SourceVerificationResult
from spec_integrator.source.python_rules import PythonRuleAnalyzer


class SourceAnalyzer:
    """Analyzes source files according to rules and group configuration."""

    def __init__(self, config: Config):
        self.config = config
        self.root_dir = config.config_dir
        self.discovery = SourceDiscovery(config)
        self.execution = SourceExecution(config)
        self.python_rules = PythonRuleAnalyzer(config)
        self.cpp_rules = CppRuleAnalyzer(self.root_dir)

    def resolve_group_names(self, group_filter: str | None = None) -> list[str]:
        return self.discovery.resolve_group_names(group_filter)

    def collect_files_for_group(
        self,
        group_name: str,
        explicit_files: list[str | Path] | None = None,
    ) -> list[Path]:
        return self.discovery.collect_files(group_name, explicit_files)

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
            if cid == "gate_checks":
                for f in files:
                    result.issues.extend(
                        self._check_gate_rules(
                            f,
                            check_rule.rules,
                            group_name,
                            rtti_exclude_paths=check_rule.rtti_exclude_paths,
                            raise_exclude_paths=check_rule.raise_exclude_paths,
                            test_backdoor_exclude_paths=check_rule.test_backdoor_exclude_paths,
                            forbidden_product_symbols=check_rule.forbidden_product_symbols,
                            non_none_union_exclude_paths=check_rule.non_none_union_exclude_paths,
                            string_member_exclude_paths=check_rule.string_member_exclude_paths,
                            object_type_exclude_paths=check_rule.object_type_exclude_paths,
                            none_default_fill_exclude_paths=check_rule.none_default_fill_exclude_paths,
                        )
                    )
            elif cid == "cpp_rules":
                for f in files:
                    result.issues.extend(
                        self.cpp_rules.check_cpp_rules(f, check_rule.rules, group_name)
                    )
            elif cid == "ruff":
                py_files = [f for f in files if f.suffix.lower() == ".py"]
                if py_files:
                    result.issues.extend(self._run_ruff(py_files, group_name))
            elif cid == "complexity":
                result.issues.extend(self.execution.run_complexity(files, group_name))
            elif cid in ("execute", "execute_formal"):
                for f in files:
                    if f.suffix.lower() == ".py":
                        result.issues.extend(self._execute_python_file(f, group_name))
            elif cid == "run_tests":
                # Pysim source verification runs the unit-test suite only.
                # Scenarios and benchmarks are checked statically but use separate runners.
                result.issues.extend(self._run_pysim_tests(group_name))
            elif cid == "pysim_imports":
                result.issues.extend(self._check_pysim_imports(group_name))
            elif cid == "pysim_assert_side_effects":
                root = self.root_dir / self.config.pysim_imports.root
                for file_path in files:
                    if not file_path.is_relative_to(root) or file_path.suffix != ".py":
                        continue
                    result.issues.extend(
                        self._check_gate_rules(
                            file_path, ["forbid_assert_side_effects"], group_name
                        )
                    )

        return result

    def _check_gate_rules(
        self,
        file_path: Path,
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
        issues: list[SourceIssue] = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as error:
            return [
                SourceIssue(
                    file_path=str(file_path.relative_to(self.root_dir)),
                    line=1,
                    rule="SABOTAGE-READ-ERROR",
                    severity="ERROR",
                    message=f"Failed to read file: {error}",
                    group=group_name,
                )
            ]

        rel_path = (
            str(file_path.relative_to(self.root_dir)).replace("\\", "/")
            if file_path.is_relative_to(self.root_dir)
            else str(file_path)
        )
        if file_path.suffix.lower() == ".py":
            issues.extend(
                self.python_rules._check_python_rules(
                    file_path=file_path,
                    content=content,
                    rel_path=rel_path,
                    rules=rules,
                    group_name=group_name,
                    rtti_exclude_paths=rtti_exclude_paths or [],
                    raise_exclude_paths=raise_exclude_paths or [],
                    test_backdoor_exclude_paths=test_backdoor_exclude_paths or [],
                    forbidden_product_symbols=forbidden_product_symbols or [],
                    non_none_union_exclude_paths=non_none_union_exclude_paths or [],
                    string_member_exclude_paths=string_member_exclude_paths or [],
                    object_type_exclude_paths=object_type_exclude_paths,
                    none_default_fill_exclude_paths=none_default_fill_exclude_paths,
                )
            )
        else:
            issues.extend(
                self.cpp_rules.check_gate_rules(file_path, content, rel_path, rules, group_name)
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

    def _run_ruff(self, files: list[Path], group_name: str) -> list[SourceIssue]:
        return self.execution.run_ruff(files, group_name)

    def _execute_python_file(self, file_path: Path, group_name: str) -> list[SourceIssue]:
        return self.execution.execute_python_file(file_path, group_name)

    def _run_pysim_tests(self, group_name: str) -> list[SourceIssue]:
        return self.execution.run_pysim_tests(group_name)
