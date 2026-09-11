import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from spec_integrator.config import Config
from spec_integrator.source_verifier import SourceIssue, SourceVerifier


def _check_python(tmp_path: Path, source: str, rules: list[str]) -> list[SourceIssue]:
    source_file = tmp_path / "sample.py"
    source_file.write_text(source, encoding="utf-8")
    config = Config()
    config.config_dir = tmp_path
    verifier = SourceVerifier(config)
    return verifier._check_anti_sabotage(source_file, rules, "python")


def test_python_static_checks_ignore_strings_and_docstrings(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        '''"""TODO and typing.Any in documentation are not source violations."""
value = "typing.Any"
# TODO: this actual work marker is reported
from typing import Any
import typing as type_api
value_with_alias: type_api.Any
''',
        ["todo_comment", "forbid_typing_any"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("SABOTAGE-TODO-COMMENT", 3),
        ("PY-FORBIDDEN-TYPING-ANY", 4),
        ("PY-FORBIDDEN-TYPING-ANY", 6),
    ]


def test_python_static_checks_use_ast_for_empty_functions_and_guards(
    tmp_path: Path,
) -> None:
    issues = _check_python(
        tmp_path,
        '''def placeholder():
    pass

def omitted():
    ...

def build_model(guards: bool):
    return guards

build_model(guards=False)
''',
        ["empty_function", "mutation_guards_required"],
    )

    assert [issue.rule for issue in issues] == [
        "SABOTAGE-EMPTY-FUNCTION",
        "SABOTAGE-EMPTY-FUNCTION",
    ]


def test_python_static_checks_report_ast_syntax_errors(tmp_path: Path) -> None:
    issues = _check_python(tmp_path, "def broken(:\n    pass\n", ["forbid_typing_any"])

    assert len(issues) == 1
    assert issues[0].rule == "PY-SYNTAX-ERROR"
