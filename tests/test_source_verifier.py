from pathlib import Path

from spec_integrator.config import (
    Config,
    PysimImportConfig,
    PysimImportTierConfig,
    SourceCheckRule,
    SourceGroupConfig,
)
from spec_integrator.source import SourceIssue
from spec_integrator.source.analyzer import SourceAnalyzer


def _check_python(tmp_path: Path, source: str, rules: list[str]) -> list[SourceIssue]:
    source_file = tmp_path / "sample.py"
    source_file.write_text(source, encoding="utf-8")
    config = Config()
    config.config_dir = tmp_path
    analyzer = SourceAnalyzer(config)
    return analyzer._check_anti_sabotage(source_file, rules, "python")


def test_assert_side_effects_cover_multiline_and_non_push_calls(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """assert queue.enqueue_front(task)
assert table.insert(
    key, value,
)
assert types.extend(params)
assert frame.values.push_i64(value)
assert state.pop_label(depth) == expected
assert _PYOBJECT_GET_BUFFER(owner, view, flags) == 0
assert stack.pop_i32() == 3
assert stack.pop_f64() == 1.5
assert vector.reverse_in_place() is None
assert storage.put(1, 2)
assert stack.set_size(0) is None
assert stack.truncate(0) is None
""",
        ["forbid_assert_side_effects"],
    )
    assert [(issue.rule, issue.line) for issue in issues] == [
        ("PY-ASSERT-SIDE-EFFECT", line) for line in (1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
    ]


def test_assert_side_effect_check_preserves_pure_observations(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """inserted = table.insert(key, value)
assert inserted
assert len(table) == expected
assert table.view().find(key) == value
text = "assert queue.enqueue(task)"
# assert queue.enqueue(task)
""",
        ["forbid_assert_side_effects"],
    )
    assert issues == []


def test_pysim_assert_check_uses_product_tier_scope(tmp_path: Path) -> None:
    product = tmp_path / "sim" / "core" / "kernel.py"
    qa = tmp_path / "sim" / "qa" / "test_kernel.py"
    for file_path in (product, qa):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("assert queue.enqueue(task)\n", encoding="utf-8")
    config = Config()
    config.config_dir = tmp_path
    config.pysim_imports = PysimImportConfig(
        root="sim", tiers=[PysimImportTierConfig(tier=1, paths=["core/**/*.py"])]
    )
    config.source_verification.groups["python_pysim"] = SourceGroupConfig(
        include_dirs=["sim"],
        extensions=[".py"],
        checks=[SourceCheckRule(id="pysim_assert_side_effects", enabled=True)],
    )
    analyzer = SourceAnalyzer(config)
    result = analyzer.verify_group("python_pysim", [product, qa])
    assert [(issue.file_path, issue.rule) for issue in result.issues] == [
        ("sim/core/kernel.py", "PY-ASSERT-SIDE-EFFECT")
    ]
    assert analyzer.verify_group("python_pysim", [qa]).issues == []


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


def test_explicit_source_files_respect_group_include_directories(tmp_path: Path) -> None:
    docs_file = tmp_path / "docs" / "sample_concept.py"
    pysim_file = tmp_path / "experiments" / "pysim" / "sample.py"
    docs_file.parent.mkdir(parents=True)
    pysim_file.parent.mkdir(parents=True)
    docs_file.write_text("pass\n", encoding="utf-8")
    pysim_file.write_text("pass\n", encoding="utf-8")

    config = Config()
    config.config_dir = tmp_path
    config.source_verification.groups["python_pysim"] = SourceGroupConfig(
        include_dirs=["experiments/pysim"], extensions=[".py"]
    )
    files = SourceAnalyzer(config).collect_files_for_group("python_pysim", [docs_file, pysim_file])

    assert files == [pysim_file.resolve()]


def test_python_static_checks_use_ast_for_empty_functions_and_guards(
    tmp_path: Path,
) -> None:
    issues = _check_python(
        tmp_path,
        """def placeholder():
    pass

def omitted():
    ...

def build_model(guards: bool):
    return guards

build_model(guards=False)
""",
        ["empty_function", "mutation_guards_required"],
    )

    assert [issue.rule for issue in issues] == [
        "SABOTAGE-EMPTY-FUNCTION",
        "SABOTAGE-EMPTY-FUNCTION",
    ]


def test_python_static_checks_allow_protocol_contract_bodies(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """from typing import Protocol

class Contract(Protocol):
    def required(self, value: int) -> str: ...

def placeholder() -> None:
    ...
""",
        ["empty_function"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("SABOTAGE-EMPTY-FUNCTION", 6),
    ]


def test_generic_protocol_aliases_only_exempt_direct_contract_methods(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """import typing as t
from typing import Protocol as P, TypeVar
T = TypeVar('T')
class First(P[T]):
    def read(self) -> T: ...
    def implemented(self):
        def omitted(): ...
        return omitted
class Second(t.Protocol[T]):
    def read(self) -> T: ...
class Concrete:
    def read(self) -> T: ...
""",
        ["empty_function"],
    )
    assert [(issue.rule, issue.line) for issue in issues] == [
        ("SABOTAGE-EMPTY-FUNCTION", 12),
        ("SABOTAGE-EMPTY-FUNCTION", 7),
    ]


def test_overload_signatures_require_concrete_implementation_in_same_scope(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """import typing as t
from typing import overload as signature
class Contract:
    @classmethod
    @signature
    def of(cls, value: int) -> int: ...
    @classmethod
    @t.overload
    def of(cls, value: str) -> str: ...
    @classmethod
    def of(cls, value): return value
class Orphan:
    @signature
    def of(self, value: int) -> int: ...
@signature
def missing(value: int) -> int: ...
def missing(value): pass
""",
        ["empty_function"],
    )
    assert sorted(issue.line for issue in issues) == [14, 16, 17]


def test_python_static_checks_report_ast_syntax_errors(tmp_path: Path) -> None:
    issues = _check_python(tmp_path, "def broken(:\n    pass\n", ["forbid_typing_any"])

    assert len(issues) == 1
    assert issues[0].rule == "PY-SYNTAX-ERROR"


def test_python_pysim_rejects_builtin_list_dict_and_set(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """
values: list[int] = [1, 2]
mapping = dict()
flags = {1, 2}
items = list(values)
""",
        ["forbid_builtin_containers"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("PY-FORBIDDEN-BUILTIN-LIST", 2),
        ("PY-FORBIDDEN-BUILTIN-LIST", 2),
        ("PY-FORBIDDEN-BUILTIN-DICT", 3),
        ("PY-FORBIDDEN-BUILTIN-SET", 4),
        ("PY-FORBIDDEN-BUILTIN-LIST", 5),
    ]


def test_python_pysim_rejects_tuple_rebuild_and_concat(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """
values: tuple[int, ...] = (1,)
other: tuple[int, ...] = (2,)
merged = values + other
rebuilt = tuple(values)
generated = tuple(value for value in values)
expanded = (*values, *other)
""",
        ["forbid_builtin_containers"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("PY-FORBIDDEN-TUPLE-CONCAT", 4),
        ("PY-FORBIDDEN-TUPLE-REBUILD", 5),
        ("PY-FORBIDDEN-TUPLE-REBUILD", 6),
        ("PY-FORBIDDEN-TUPLE-REBUILD", 7),
    ]
    assert "Reason:" in issues[1].message
    assert "Resource requirement:" in issues[1].message


def test_python_pysim_allows_callable_parameter_type_syntax(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        "from collections.abc import Callable\nhandler: Callable[[int, int], int]\n",
        ["forbid_builtin_containers"],
    )

    assert issues == []


def test_python_pysim_rejects_object_and_non_none_union(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        "value: object\nchoice: int | str\nnullable: int | None\n",
        ["forbid_object_type", "forbid_non_none_union"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("PY-FORBIDDEN-OBJECT-TYPE", 1),
        ("PY-FORBIDDEN-NON-NONE-UNION", 2),
    ]


def test_python_pysim_rejects_runtime_string_members_but_allows_constants(
    tmp_path: Path,
) -> None:
    issues = _check_python(
        tmp_path,
        """class Metadata:
    name: str
    labels: ReadOnlyFlatMapView[int, str]
    constant_name: str = "ROM_NAME"

    def __init__(self) -> None:
        self.runtime_name: str = ""

def local(value: str) -> int:
    return 0
""",
        ["forbid_string_members"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("PY-FORBIDDEN-STRING-MEMBER", 2),
        ("PY-FORBIDDEN-STRING-MEMBER", 3),
        ("PY-FORBIDDEN-STRING-MEMBER", 7),
    ]


def test_python_pysim_rejects_non_none_union_in_special_method_signature(
    tmp_path: Path,
) -> None:
    issues = _check_python(
        tmp_path,
        "def __getitem__(self, index: int | slice) -> int | tuple[int, ...]: ...\n",
        ["forbid_non_none_union"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("PY-FORBIDDEN-NON-NONE-UNION", 1),
        ("PY-FORBIDDEN-NON-NONE-UNION", 1),
    ]


def test_python_pysim_rejects_raise_but_allows_except(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """def catches(value: int) -> int:
    try:
        return int(value)
    except ValueError:
        return 0

def sends(value: int) -> int:
    raise ValueError(value)
""",
        ["forbid_raise"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("PY-FORBIDDEN-RAISE", 8),
    ]


def test_python_pysim_rejects_rtti_and_in_operator(tmp_path: Path) -> None:
    issues = _check_python(
        tmp_path,
        """def lookup(value: int, values: tuple[int, ...]) -> bool:
    return value in values

def inspect(value: int) -> bool:
    return isinstance(value, int)
""",
        ["forbid_rtti", "forbid_in_operator"],
    )

    assert [(issue.rule, issue.line) for issue in issues] == [
        ("PY-FORBIDDEN-RTTI", 5),
        ("PY-FORBIDDEN-IN-OPERATOR", 2),
    ]


def test_python_pysim_container_exclusion_is_path_configured(tmp_path: Path) -> None:
    analyzer = SourceAnalyzer(Config())
    issues = analyzer._check_python_rules(
        tmp_path / "system_containers.py",
        "values: list[int] = [1, 2]\n",
        "experiments/pysim/tier1_core/system_containers.py",
        ["forbid_builtin_containers"],
        "python_pysim",
        ["experiments/pysim/tier1_core/system_containers.py"],
    )

    assert issues == []


def test_pysim_import_check_uses_configured_file_tiers(tmp_path: Path) -> None:
    pysim_root = tmp_path / "experiments" / "pysim"
    (pysim_root / "tier1").mkdir(parents=True)
    (pysim_root / "tier2").mkdir(parents=True)
    (pysim_root / "tier1" / "contract.py").write_text(
        "from implementation import Value\n", encoding="utf-8"
    )
    (pysim_root / "tier2" / "implementation.py").write_text("class Value: pass\n", encoding="utf-8")

    config = Config()
    config.config_dir = tmp_path
    config.pysim_imports = PysimImportConfig(
        root="experiments/pysim",
        tiers=[
            PysimImportTierConfig(tier=1, paths=["tier1/*.py"]),
            PysimImportTierConfig(tier=2, paths=["tier2/*.py"]),
        ],
    )

    issues = SourceAnalyzer(config)._check_pysim_imports("python_pysim")

    assert [(issue.rule, issue.line) for issue in issues] == [("PYSIM-IMPORT-DIRECTION", 1)]
