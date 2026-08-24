import json
import pytest
from pathlib import Path
from spec_integrator.config import Config, KeywordRule
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.consistency import ConsistencyVerifier, _normalize_value


def _docs(tmp_path, files: dict[str, str]):
    docs_dir = tmp_path / "docs"
    cfg = Config()
    cfg.config_dir = tmp_path
    # Keyword definitions live in requires/**; everything else is a reference.
    cfg.keywords = {
        "local": KeywordRule(pattern=r"^[A-Za-z0-9_]+$", defined_in=r"requires/.*\.md")
    }
    parser = MarkdownParser(cfg)
    parsed = []
    for rel, body in files.items():
        p = docs_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        parsed.append(parser.parse_file(p, docs_dir))
    return cfg, parsed, docs_dir


# --------------------------------------------------------------------------- #
# Value normalization
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("6144", "6144"), ("6144U", "6144"), ("6_144", "6144"),
    ("6KB", "6144"), ("6.0 KB", "6144"), ("6144 Bytes", "6144"),
    ("0x80000000", "2147483648"), ("0x8000_0000", "2147483648"),
])
def test_value_spellings_collapse(raw, expected):
    assert _normalize_value(raw) == expected


# --------------------------------------------------------------------------- #
# B. Symbol drift
# --------------------------------------------------------------------------- #
def test_conflicting_symbol_values_are_detected(tmp_path):
    cfg, docs, docs_root = _docs(tmp_path, {
        "components/tier1_core/a.md": "# A\n## Config\n"
                                      "| `FB_CONF_JIT_CACHE_SIZE` | JIT cache | `6144` | note |\n",
        "components/tier1_core/b.md": "# B\n## Config\n"
                                      "* **`FB_CONF_JIT_CACHE_SIZE`**: サイズ（デフォルト値: 4096バイト）。\n",
    })
    cfg.consistency.cochange = False
    issues, summary = ConsistencyVerifier(cfg).verify(docs, docs_root)

    drift = [i for i in issues if i.rule_code == "CONSIST-SYMBOL-DRIFT"]
    assert len(drift) == 1
    assert "6144" in drift[0].message and "4096" in drift[0].message
    assert summary.drifting_symbols[0].symbol == "FB_CONF_JIT_CACHE_SIZE"


def test_equivalent_spellings_are_not_drift(tmp_path):
    cfg, docs, docs_root = _docs(tmp_path, {
        "components/tier1_core/a.md": "# A\n## C\n| `FB_CONF_JIT_CACHE_SIZE` | x | `6144` | n |\n",
        "components/tier1_core/b.md": "# B\n## C\n* `FB_CONF_JIT_CACHE_SIZE`: デフォルト値: 6KB\n",
    })
    cfg.consistency.cochange = False
    issues, _ = ConsistencyVerifier(cfg).verify(docs, docs_root)
    assert [i for i in issues if i.rule_code == "CONSIST-SYMBOL-DRIFT"] == []


def test_prose_components_are_not_mistaken_for_the_value(tmp_path):
    """'2KB x 3 = 6144' states parts and a total; only the total is the value."""
    cfg, docs, docs_root = _docs(tmp_path, {
        "components/tier1_core/a.md": "# A\n## C\n| `FB_CONF_JIT_CACHE_SIZE` | x | `6144` | n |\n",
        "components/tier2_jit/b.md": "# B\n## C\n"
                                     "- マルチバッファ (3面: 2KB x 3 = 6144 Bytes `FB_CONF_JIT_CACHE_SIZE`)。\n",
    })
    cfg.consistency.cochange = False
    issues, _ = ConsistencyVerifier(cfg).verify(docs, docs_root)
    assert [i for i in issues if i.rule_code == "CONSIST-SYMBOL-DRIFT"] == []


def test_constraint_mentions_are_not_the_value(tmp_path):
    """'FB_CONF_MAX_TASKS は 254 以下' is a bound, not a declaration."""
    cfg, docs, docs_root = _docs(tmp_path, {
        "components/tier1_core/a.md": "# A\n## C\n| `FB_CONF_MAX_TASKS` | tasks | `8` | n |\n",
        "components/tier1_core/b.md": "# B\n## C\n"
                                      "- `FB_CONF_MAX_TASKS` の値は 254 以下でなければならない。\n",
    })
    cfg.consistency.cochange = False
    issues, _ = ConsistencyVerifier(cfg).verify(docs, docs_root)
    assert [i for i in issues if i.rule_code == "CONSIST-SYMBOL-DRIFT"] == []


def test_symbol_named_in_a_later_cell_is_a_reference_not_a_declaration(tmp_path):
    cfg, docs, docs_root = _docs(tmp_path, {
        "components/tier1_core/a.md": "# A\n## C\n| `FB_CONF_MAX_TASKS` | tasks | `8` | n |\n",
        "components/tier1_core/b.md": "# B\n## C\n"
                                      "| `FB_TASK_ID_FLIGHT` | sentinel | `0xFF` | "
                                      "`FB_CONF_MAX_TASKS` は 0xFE 以下 |\n",
    })
    cfg.consistency.cochange = False
    issues, _ = ConsistencyVerifier(cfg).verify(docs, docs_root)
    assert [i for i in issues if i.rule_code == "CONSIST-SYMBOL-DRIFT"] == []


def test_code_blocks_are_skipped(tmp_path):
    cfg, docs, docs_root = _docs(tmp_path, {
        "components/tier1_core/a.md": "# A\n## C\n| `FB_CONF_MAX_TASKS` | t | `8` | n |\n",
        "components/tier1_core/b.md": "# B\n## C\n```python\nFB_CONF_MAX_TASKS = 99\n```\n",
    })
    cfg.consistency.cochange = False
    issues, _ = ConsistencyVerifier(cfg).verify(docs, docs_root)
    assert [i for i in issues if i.rule_code == "CONSIST-SYMBOL-DRIFT"] == []


def test_extra_scan_globs_pull_in_headers(tmp_path):
    cfg, docs, docs_root = _docs(tmp_path, {
        "components/tier1_core/a.md": "# A\n## C\n| `FB_CONF_VMMIO_BASE` | base | `0x80000000` | n |\n",
    })
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "fireball_config.hxx").write_text(
        "static constexpr std::uint32_t FB_CONF_VMMIO_BASE = 0x40000000U;\n", encoding="utf-8")

    cfg.consistency.cochange = False
    cfg.consistency.extra_scan_globs = ["inc/*.hxx"]
    issues, _ = ConsistencyVerifier(cfg).verify(docs, docs_root)

    drift = [i for i in issues if i.rule_code == "CONSIST-SYMBOL-DRIFT"]
    assert len(drift) == 1
    assert "1073741824" in drift[0].message and "2147483648" in drift[0].message


# --------------------------------------------------------------------------- #
# A. Stale values
# --------------------------------------------------------------------------- #
def test_superseded_value_is_rejected(tmp_path):
    cfg, docs, docs_root = _docs(tmp_path, {
        "architecture/FORMAT.md": "# T\n## Ex\n| JIT | vSoC | 4 KB (2KB x 2) | ダブルバッファ |\n",
    })
    cfg.consistency.cochange = False
    cfg.consistency.invariants = [{
        "id": "jit_banks",
        "reason": "JIT キャッシュは 3 面 6144 バイトに移行済み",
        "canonical": "6144 (2KB x 3)",
        "forbidden": [r"2KB\s*[x×]\s*2", "ダブルバッファ"],
        "scope": ["**/*.md"],
    }]
    issues, summary = ConsistencyVerifier(cfg).verify(docs, docs_root)
    stale = [i for i in issues if i.rule_code == "CONSIST-STALE-VALUE"]
    assert len(stale) == 2
    assert summary.invariants_checked == 1


# --------------------------------------------------------------------------- #
# C. Co-change
# --------------------------------------------------------------------------- #
REQ = """# Requirements
## Functional
| `{JIT_MultiBuffer_Cache}` | 3面バッファ管理を行う。 | 高 |
| `{ROMParsing}` | ROM を直接解析する。 | 高 |
"""
COMP_A = "# JIT\n## Design\nマルチバッファを用いる。 `{JIT_MultiBuffer_Cache}`\n"
COMP_B = "# Loader\n## Design\nROM を直接読む。 `{ROMParsing}`\n"


def _cochange_setup(tmp_path):
    cfg, docs, docs_root = _docs(tmp_path, {
        "requires/requirement_list.md": REQ,
        "components/tier2_jit/jit.md": COMP_A,
        "components/tier2_runtime/loader.md": COMP_B,
    })
    return cfg, docs, docs_root


def test_baseline_missing_is_a_warning_not_a_pass(tmp_path):
    cfg, docs, docs_root = _cochange_setup(tmp_path)
    issues, summary = ConsistencyVerifier(cfg).verify(docs, docs_root)
    assert any(i.rule_code == "CONSIST-BASELINE-MISSING" for i in issues)
    assert summary.baseline_present is False


def test_changed_definition_flags_only_its_own_references(tmp_path):
    cfg, docs, docs_root = _cochange_setup(tmp_path)
    v = ConsistencyVerifier(cfg)
    v.write_baseline(docs)

    # Edit one requirement row; neighbours in the same table must stay unaffected.
    req = docs_root / "requires" / "requirement_list.md"
    req.write_text(REQ.replace("3面バッファ管理を行う。", "4面バッファ管理を行う。"), encoding="utf-8")

    parser = MarkdownParser(cfg)
    reparsed = [parser.parse_file(p, docs_root) for p in sorted(docs_root.rglob("*.md"))]

    issues, summary = ConsistencyVerifier(cfg).verify(reparsed, docs_root)
    stale = [i for i in issues if i.rule_code == "CONSIST-COCHANGE-STALE"]

    assert len(stale) == 1
    assert stale[0].file_path == "components/tier2_jit/jit.md"
    assert summary.cochange_stale[0]["keyword"] == "JIT_MultiBuffer_Cache"


def test_reference_updated_in_the_same_edit_is_not_flagged(tmp_path):
    cfg, docs, docs_root = _cochange_setup(tmp_path)
    v = ConsistencyVerifier(cfg)
    v.write_baseline(docs)

    (docs_root / "requires" / "requirement_list.md").write_text(
        REQ.replace("3面バッファ管理を行う。", "4面バッファ管理を行う。"), encoding="utf-8")
    (docs_root / "components" / "tier2_jit" / "jit.md").write_text(
        "# JIT\n## Design\n4面マルチバッファを用いる。 `{JIT_MultiBuffer_Cache}`\n", encoding="utf-8")

    parser = MarkdownParser(cfg)
    reparsed = [parser.parse_file(p, docs_root) for p in sorted(docs_root.rglob("*.md"))]

    issues, _ = ConsistencyVerifier(cfg).verify(reparsed, docs_root)
    assert [i for i in issues if i.rule_code == "CONSIST-COCHANGE-STALE"] == []


def test_sync_accepts_the_current_state(tmp_path):
    cfg, docs, docs_root = _cochange_setup(tmp_path)
    v = ConsistencyVerifier(cfg)
    v.write_baseline(docs)
    issues, _ = v.verify(docs, docs_root)
    assert [i for i in issues if i.rule_code.startswith("CONSIST-COCHANGE")] == []


def test_gate_can_be_disabled(tmp_path):
    cfg, docs, docs_root = _cochange_setup(tmp_path)
    cfg.consistency.enabled = False
    issues, _ = ConsistencyVerifier(cfg).verify(docs, docs_root)
    assert issues == []


# --------------------------------------------------------------------------- #
# Duplicate definitions
# --------------------------------------------------------------------------- #
def test_duplicate_keyword_definition_is_reported(tmp_path):
    """A keyword defined on two rows has two authorities. Whichever row an editor
    updates becomes 'the' definition and the twin keeps the old wording while
    still satisfying the traceability gate -- this is how {HAL_Interface} sat
    duplicated in fireball's requirement_list.md through every prior gate run."""
    cfg, parsed, docs_dir = _docs(tmp_path, {
        "requires/requirement_list.md": (
            "# Requirements\n"
            "## 3.1 機能要求\n"
            "| キーワード | 内容 | 優先度 |\n"
            "| :--- | :--- | :--- |\n"
            "| `{HAL_Interface}` | 物理デバイス操作を抽象化する。 | 高 |\n"
            "| `{OtherThing}` | 別の要求。 | 中 |\n"
            "| `{HAL_Interface}` | 物理デバイス操作を抽象化する。 | 高 |\n"
        ),
    })

    issues, _ = ConsistencyVerifier(cfg).verify(parsed, docs_dir)
    dups = [i for i in issues if i.rule_code == "CONSIST-DUPLICATE-DEFINITION"]
    assert len(dups) == 1, "the duplicated definition must be reported exactly once"
    assert "HAL_Interface" in dups[0].message
    assert "OtherThing" not in dups[0].message


def test_a_keyword_defined_once_is_not_reported(tmp_path):
    """Citing a keyword inside another row, or in prose, is an ordinary reference
    and must not be mistaken for a second definition."""
    cfg, parsed, docs_dir = _docs(tmp_path, {
        "requires/requirement_list.md": (
            "# Requirements\n"
            "## 3.1 機能要求\n"
            "| キーワード | 内容 | 優先度 |\n"
            "| :--- | :--- | :--- |\n"
            "| `{HAL_Interface}` | 物理デバイス操作を抽象化する。 | 高 |\n"
            "| `{OtherThing}` | `{HAL_Interface}` を利用する別の要求。 | 中 |\n"
            "\n本文中で `{HAL_Interface}` に言及する。\n"
        ),
    })

    issues, _ = ConsistencyVerifier(cfg).verify(parsed, docs_dir)
    assert [i for i in issues if i.rule_code == "CONSIST-DUPLICATE-DEFINITION"] == []


def test_a_duplicate_outside_a_definition_file_is_not_reported(tmp_path):
    """Design documents restate keywords in tables all the time; only the
    definition source can hold a duplicate *definition*."""
    cfg, parsed, docs_dir = _docs(tmp_path, {
        "components/os.md": (
            "# Design\n"
            "## 1. 概要\n"
            "| キーワード | 内容 |\n"
            "| :--- | :--- |\n"
            "| `{HAL_Interface}` | ここでの説明。 |\n"
            "| `{HAL_Interface}` | 別の観点からの説明。 |\n"
        ),
    })

    issues, _ = ConsistencyVerifier(cfg).verify(parsed, docs_dir)
    assert [i for i in issues if i.rule_code == "CONSIST-DUPLICATE-DEFINITION"] == []
