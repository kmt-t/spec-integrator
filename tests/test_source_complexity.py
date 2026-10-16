from pathlib import Path

import yaml
from spec_integrator.config import Config
from spec_integrator.source.analyzer import SourceAnalyzer
from spec_integrator.source.service import SourceService


def test_project_complexity_settings_have_one_threshold_and_all_baselines() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    config = Config.load(repository_root / "spec-integrator.yaml")

    complexity = config.source_verification.complexity
    assert complexity.threshold == 15
    assert set(complexity.targets) == {"cpp", "python_pysim"}
    assert len(complexity.allowlist) == 22
    assert all(entry.ccn > complexity.threshold for entry in complexity.allowlist)
    for group_name in ("cpp", "python_pysim"):
        group = config.source_verification.groups[group_name]
        assert any(check.id == "complexity" and check.enabled for check in group.checks)


def test_project_complexity_allowlist_matches_all_current_functions() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    analyzer = SourceAnalyzer(Config.load(repository_root / "spec-integrator.yaml"))

    for group_name in ("cpp", "python_pysim"):
        files = analyzer.collect_files_for_group(group_name)
        assert analyzer.execution.run_complexity(files, group_name) == []


def _write_branch_function(path: Path, branch_count: int, function_name: str = "measured") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"def {function_name}(value: int) -> int:"]
    lines.extend(f"    if value == {index}: return {index}" for index in range(branch_count))
    lines.append("    return -1")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_cpp_branch_function(path: Path, branch_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["int measured(int value) {"]
    lines.extend(f"  if (value == {index}) return {index};" for index in range(branch_count))
    lines.extend(("  return -1;", "}"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_config(root: Path, allowlist: list[dict[str, str | int]]) -> Config:
    config_path = root / "spec-integrator.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "source_verification": {
                    "complexity": {
                        "threshold": 15,
                        "targets": {
                            "python_pysim": {
                                "include_paths": ["experiments/pysim/**"],
                                "exclude_paths": [
                                    "experiments/pysim/qa/**",
                                    "experiments/pysim/benchmarks/**",
                                    "experiments/pysim/native/**",
                                ],
                                "extensions": [".py"],
                            },
                            "cpp": {
                                "include_paths": ["inc/**", "src/**"],
                                "exclude_paths": ["src/allocator/malloc.c"],
                                "extensions": [".hxx", ".cxx", ".c"],
                            },
                        },
                        "allowlist": allowlist,
                    },
                    "groups": {
                        "python_pysim": {
                            "include_dirs": ["experiments/pysim"],
                            "extensions": [".py"],
                            "checks": [{"id": "complexity", "enabled": True}],
                        },
                        "cpp": {
                            "include_dirs": ["inc", "src"],
                            "extensions": [".hxx", ".cxx", ".c"],
                            "checks": [{"id": "complexity", "enabled": True}],
                        },
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return Config.load(config_path)


def test_complexity_reports_ccn_16_with_function_location_and_line_count(
    tmp_path: Path,
) -> None:
    _write_branch_function(tmp_path / "experiments/pysim/tier1_core/sample.py", 15)
    service = SourceService(_make_config(tmp_path, []))

    results = service.check(group="pysim")

    assert len(results) == 1
    issues = results[0].issues
    assert len(issues) == 1
    assert issues[0].rule == "SRC-COMPLEXITY"
    assert issues[0].severity == "ERROR"
    assert issues[0].file_path == "experiments/pysim/tier1_core/sample.py"
    assert issues[0].line == 1
    assert "measured" in issues[0].message
    assert "CCN 16" in issues[0].message
    assert "limit 15" in issues[0].message
    assert "lines" in issues[0].message


def test_complexity_requires_threshold_to_be_configured(tmp_path: Path) -> None:
    file_path = tmp_path / "experiments/pysim/tier1_core/sample.py"
    _write_branch_function(file_path, 15)
    config = _make_config(tmp_path, [])
    config.source_verification.complexity.threshold = None
    service = SourceService(config)

    results = service.check(group="pysim")

    assert len(results) == 1
    assert len(results[0].issues) == 1
    assert results[0].issues[0].rule == "SRC-COMPLEXITY-CONFIG"


def test_complexity_accepts_allowlisted_ccn_and_rejects_an_increase(
    tmp_path: Path,
) -> None:
    file_path = "experiments/pysim/tier1_core/sample.py"
    _write_branch_function(tmp_path / file_path, 16)
    allowance = {
        "group": "python_pysim",
        "file": file_path,
        "function": "measured",
        "line": 1,
        "ccn": 16,
        "plan_req": "REQ-042",
    }
    service = SourceService(_make_config(tmp_path, [allowance]))

    results = service.check(group="pysim")

    assert len(results) == 1
    assert len(results[0].issues) == 1
    issue = results[0].issues[0]
    assert issue.rule == "SRC-COMPLEXITY-RATCHET"
    assert issue.severity == "ERROR"
    assert "baseline 16" in issue.message
    assert "CCN 17" in issue.message


def test_complexity_warns_when_allowlisted_function_reaches_the_limit(
    tmp_path: Path,
) -> None:
    file_path = "experiments/pysim/tier1_core/sample.py"
    _write_branch_function(tmp_path / file_path, 14)
    allowance = {
        "group": "python_pysim",
        "file": file_path,
        "function": "measured",
        "line": 1,
        "ccn": 16,
        "plan_req": "REQ-042",
    }
    service = SourceService(_make_config(tmp_path, [allowance]))

    results = service.check(group="pysim")

    assert len(results) == 1
    assert len(results[0].issues) == 1
    issue = results[0].issues[0]
    assert issue.rule == "SRC-COMPLEXITY-ALLOWLIST-STALE"
    assert issue.severity == "WARNING"
    assert "remove" in issue.message


def test_complexity_scope_excludes_qa_benchmarks_native_and_vendored_malloc(
    tmp_path: Path,
) -> None:
    product_python = tmp_path / "experiments/pysim/tier1_core/product.py"
    _write_branch_function(product_python, 15)
    _write_branch_function(tmp_path / "experiments/pysim/qa/test_ignored.py", 15)
    _write_branch_function(tmp_path / "experiments/pysim/benchmarks/ignored.py", 15)
    _write_branch_function(tmp_path / "experiments/pysim/native/ignored.py", 15)
    product_cpp = tmp_path / "src/selected.c"
    _write_cpp_branch_function(product_cpp, 15)
    _write_cpp_branch_function(tmp_path / "src/allocator/malloc.c", 15)
    service = SourceService(_make_config(tmp_path, []))

    results = service.check(group="all")

    issues = [issue for result in results for issue in result.issues]
    assert {(issue.group, issue.file_path) for issue in issues} == {
        ("python_pysim", "experiments/pysim/tier1_core/product.py"),
        ("cpp", "src/selected.c"),
    }
    assert all(issue.rule == "SRC-COMPLEXITY" for issue in issues)
