from pathlib import Path

import pytest
from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.document.gates.identifiers import IdentifierGate
from spec_integrator.document.identifier_index import next_identifier, scan_identifiers
from spec_integrator.document.workspace import load_workspace

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _config(tmp_path: Path) -> Config:
    config = Config.load(REPOSITORY_ROOT / "spec-integrator.yaml")
    config.config_dir = tmp_path
    config.project.cache_db = ".spec-integrator/test-index.sqlite"
    return config


def _write_test_spec(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Test specification\n\n## 1. Scope\n\nScope.\n\n"
        "## 2. Test cases\n\n"
        "| Test ID | Name |\n| :--- | :--- |\n" + "".join(f"| {row} | Case |\n" for row in rows),
        encoding="utf-8",
    )


def _issues(config: Config):
    scan = scan_identifiers(config)
    db = DocAuditDB(":memory:")
    db.replace_identifier_index(scan.definitions, scan.references)
    try:
        return scan, IdentifierGate(config).verify(db)
    finally:
        db.close()


def test_duplicate_identifier_definitions_are_errors(tmp_path: Path) -> None:
    _write_test_spec(
        tmp_path / "docs/qa/tier1_core/a_test_spec.md",
        ["TEST-AAA-01", "TEST-AAA-01"],
    )

    _scan, issues = _issues(_config(tmp_path))

    duplicate_errors = [issue for issue in issues if issue.rule_code == "ID-DUPLICATE-DEFINITION"]
    assert len(duplicate_errors) == 2
    assert all(issue.severity == "ERROR" for issue in duplicate_errors)
    assert all(issue.file_path == "docs/qa/tier1_core/a_test_spec.md" for issue in duplicate_errors)


def test_unresolved_reference_is_an_error(tmp_path: Path) -> None:
    _write_test_spec(tmp_path / "docs/qa/tier1_core/a_test_spec.md", ["TEST-AAA-01"])
    source = tmp_path / "experiments/pysim/qa/test_ids.py"
    source.parent.mkdir(parents=True)
    source.write_text('"""TEST-AAA-02"""\n', encoding="utf-8")

    _scan, issues = _issues(_config(tmp_path))

    unresolved = [issue for issue in issues if issue.rule_code == "ID-UNRESOLVED-REFERENCE"]
    assert len(unresolved) == 1
    assert unresolved[0].severity == "ERROR"
    assert unresolved[0].file_path == "experiments/pysim/qa/test_ids.py"
    assert unresolved[0].line == 1


def test_invalid_identifier_syntax_is_an_error(tmp_path: Path) -> None:
    source = tmp_path / "experiments/pysim/qa/test_ids.py"
    source.parent.mkdir(parents=True)
    source.write_text('"""TEST-AAA-1"""\n', encoding="utf-8")

    _scan, issues = _issues(_config(tmp_path))

    invalid = [issue for issue in issues if issue.rule_code == "ID-INVALID-FORMAT"]
    assert len(invalid) == 1
    assert invalid[0].severity == "ERROR"
    assert invalid[0].file_path == "experiments/pysim/qa/test_ids.py"


def test_unreferenced_test_gotcha_and_benchmark_definitions_warn(tmp_path: Path) -> None:
    _write_test_spec(tmp_path / "docs/qa/tier1_core/a_test_spec.md", ["TEST-AAA-01"])
    component = tmp_path / "docs/components/tier1_core/component.md"
    component.parent.mkdir(parents=True)
    component.write_text(
        "# Component\n\n## 2. Design\n\n<!-- definition: {GOTCHA-AAA-01} -->\n",
        encoding="utf-8",
    )
    benchmark = tmp_path / "docs/components/tier1_core/benchmarks/a_spec.md"
    benchmark.parent.mkdir(parents=True)
    benchmark.write_text(
        "# Benchmark\n\n## 2. Measurements\n\n"
        "| ID | Name |\n| :--- | :--- |\n| **BENCHMARK-AAA-01** | Case |\n",
        encoding="utf-8",
    )

    _scan, issues = _issues(_config(tmp_path))

    warnings = [issue for issue in issues if issue.rule_code == "ID-UNREFERENCED-DEFINITION"]
    assert {(issue.file_path, issue.message.split(":")[-1].strip()) for issue in warnings} == {
        ("docs/qa/tier1_core/a_test_spec.md", "TEST-AAA-01"),
        ("docs/components/tier1_core/component.md", "GOTCHA-AAA-01"),
        ("docs/components/tier1_core/benchmarks/a_spec.md", "BENCHMARK-AAA-01"),
    }
    assert all(issue.severity == "WARNING" for issue in warnings)


def test_range_keeps_raw_notation_and_expands_existing_ids_only(tmp_path: Path) -> None:
    _write_test_spec(
        tmp_path / "docs/qa/tier1_core/a_test_spec.md",
        ["TEST-AAA-01", "TEST-AAA-03"],
    )
    ref = tmp_path / "docs/components/tier1_core/component.md"
    ref.parent.mkdir(parents=True)
    ref.write_text("# Component\n\nCases: TEST-AAA-01..TEST-AAA-03.\n", encoding="utf-8")

    scan, issues = _issues(_config(tmp_path))

    ranges = [reference for reference in scan.references if reference.range_end is not None]
    assert len(ranges) == 1
    assert ranges[0].raw_text == "TEST-AAA-01..TEST-AAA-03"
    assert ranges[0].range_notation == ".."
    assert ranges[0].expanded_ids == ["TEST-AAA-01", "TEST-AAA-03"]
    assert not [issue for issue in issues if issue.severity == "ERROR"]


def test_reversed_range_endpoints_are_rejected(tmp_path: Path) -> None:
    _write_test_spec(
        tmp_path / "docs/qa/tier1_core/a_test_spec.md",
        ["TEST-AAA-01", "TEST-AAA-03"],
    )
    ref = tmp_path / "docs/components/tier1_core/component.md"
    ref.parent.mkdir(parents=True)
    ref.write_text("# Component\n\nCases: TEST-AAA-03..TEST-AAA-01.\n", encoding="utf-8")

    _scan, issues = _issues(_config(tmp_path))

    invalid_ranges = [issue for issue in issues if issue.rule_code == "ID-INVALID-RANGE"]
    assert len(invalid_ranges) == 1
    assert invalid_ranges[0].severity == "ERROR"


def test_sequence_gaps_are_reported_as_info_not_warning(tmp_path: Path) -> None:
    _write_test_spec(
        tmp_path / "docs/qa/tier1_core/a_test_spec.md",
        ["TEST-AAA-01", "TEST-AAA-03"],
    )

    _scan, issues = _issues(_config(tmp_path))

    reports = [issue for issue in issues if issue.rule_code == "ID-SERIAL-GAPS"]
    assert len(reports) == 1
    assert reports[0].severity == "INFO"
    assert "1 missing serial number" in reports[0].message
    assert not [
        issue
        for issue in issues
        if issue.rule_code == "ID-SERIAL-GAPS" and issue.severity == "WARNING"
    ]


def test_next_id_scans_real_series_and_asserts_when_series_is_absent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_config = Config.load(REPOSITORY_ROOT / "spec-integrator.yaml")
    assert next_identifier(project_config, "TEST-INT") == "TEST-INT-130"

    from spec_integrator.cli.parser import build_parser

    parsed = build_parser().parse_args(["next-id", "TEST-INT"])
    parsed.config = REPOSITORY_ROOT / "spec-integrator.yaml"
    assert parsed.prefix == "TEST-INT"
    assert parsed.func.__name__ == "cmd_next_id"
    with pytest.raises(SystemExit) as exit_info:
        parsed.func(parsed)
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "TEST-INT-130"

    empty_config = _config(tmp_path)
    with pytest.raises(AssertionError, match="No identifiers found"):
        next_identifier(empty_config, "TEST-EMPTY")


def test_workspace_replaces_stale_identifier_rows_even_for_partial_file_load(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    first_spec = tmp_path / "docs/qa/tier1_core/a_test_spec.md"
    second_spec = tmp_path / "docs/qa/tier1_core/b_test_spec.md"
    selected = tmp_path / "docs/qa/tier1_core/selected.md"
    _write_test_spec(first_spec, ["TEST-AAA-01"])
    _write_test_spec(second_spec, ["TEST-BBB-02"])
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text("# Selected\n\nSelected partial document.\n", encoding="utf-8")

    first_workspace = load_workspace(config, file_paths=[selected])
    first_workspace.db.close()
    stale_db = DocAuditDB(config.get_db_path())
    stale_db.conn.execute(
        """INSERT INTO identifiers
        (family, identifier, normalized_id, definition_kind, file_path, section_id,
         line, column_number, source_hash)
        VALUES ('TEST', 'TEST-STALE-99', 'TEST-STALE-99', 'case', 'stale.md', NULL, 1, 1, 'old')"""
    )
    stale_db.conn.execute(
        """INSERT INTO identifier_references
        (family, identifier, raw_text, reference_kind, file_path, section_id, line,
         column_number, range_end, range_notation, expanded_ids)
        VALUES ('TEST', 'TEST-STALE-99', 'TEST-STALE-99', 'source', 'stale.py', NULL,
                1, 1, NULL, NULL, '[]')"""
    )
    stale_db.commit()
    stale_db.close()

    _write_test_spec(first_spec, ["TEST-AAA-03"])
    second_workspace = load_workspace(config, file_paths=[selected])
    try:
        identifiers = second_workspace.db.get_identifiers()
        references = second_workspace.db.get_identifier_references()
        current_ids = {str(item["normalized_id"]) for item in identifiers}
        assert "TEST-AAA-03" in current_ids
        assert "TEST-BBB-02" in current_ids
        assert "TEST-AAA-01" not in current_ids
        assert "TEST-STALE-99" not in current_ids
        assert all(str(item["identifier"]) != "TEST-STALE-99" for item in references)
    finally:
        second_workspace.db.close()
