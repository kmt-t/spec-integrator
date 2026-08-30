import subprocess
from unittest.mock import patch

from spec_integrator.config import Config
from spec_integrator.graph import DocGraphBuilder
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.fake_decision_detector import FakeDecisionDetector


def _build(tmp_path, files: dict[str, str]):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = docs_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    cfg = Config()
    cfg.config_dir = tmp_path
    parser = MarkdownParser(cfg)
    documents = [parser.parse_file(docs_dir / rel, docs_dir) for rel in files]
    graph = DocGraphBuilder(cfg).build(documents, docs_dir)
    return cfg, documents, graph


def _keyword(findings, keyword):
    return next(f for f in findings if f.keyword == keyword)


ISOLATED_SINGLE_OPTION = """# JIT Compiler
## 8. 設計判断 (ADR)
- **決定事項**: `{ADR_Lonely}`
  - **背景**: 何かの理由。
  - **選択肢**:
    - 案1: これだけ採用する。
  - **結論**: 案1を採用する。
"""

WELL_FORMED_ADR = """# JIT Compiler
## 8. 設計判断 (ADR)
- **決定事項**: `{ADR_WellDiscussed}`
  - **背景**: レジスタが枯渇しており、複数の現実的な代替案が存在する。
  - **選択肢と評価**:
    - 案1: 案Aを採用するとレイテンシがゼロになるが、既存のスクラッチレジスタ割り当てを再設計する必要があり、全ハンドラに新しい不変条件を課すことになる。
    - 案2: 案Bを採用すると実装は単純になるが、最適化の余地の大半を放棄することになり、目標性能の達成が困難になる。
    - 案3: 非対称性を許容し、内部でのみレジスタを再利用する。
  - **結論**: 案3を採用する。
"""

STRAWMAN_ADR = """# JIT Compiler
## 8. 設計判断 (ADR)
- **決定事項**: `{ADR_Strawman}`
  - **背景**: 何かの理由でこの決定が必要になった、という長い背景説明がここに書かれている。
  - **選択肢と評価**:
    - 案1: 採用する案について、なぜこれが最善なのかを詳細に説明する長い文章がここに続き、比較のための具体的な根拠が示される。
    - 案2: だめ。
  - **結論**: 案1を採用する。
"""

REFERENCING_DOC = """# Other Component
## 1. コンセプト
This design relies on {ADR_WellDiscussed} for its register convention.
"""

HEADING_STYLE = """# Scheduler
## 6. 設計判断 (ADR)
### ADR-SCHED-001: 侵入型リストによる管理
<!-- traceability: {GLOBAL_Policy_Memory} -->
- **決定事項**: TCBの連結には侵入型リストを採用する。
- **理由**: 動的メモリ確保を排除するため。

### ADR-SCHED-002: 次の話題
- 決定事項なし、ただの後続セクション。
"""


def test_isolated_single_option_adr_is_flagged(tmp_path):
    cfg, docs, graph = _build(
        tmp_path, {"components/tier3_jit/jit_compiler.md": ISOLATED_SINGLE_OPTION}
    )
    findings = FakeDecisionDetector(cfg, repo_root=tmp_path).verify(docs, graph)
    f = _keyword(findings, "ADR_Lonely")
    assert f.is_bracket_keyword
    assert f.referenced_file_count == 0
    assert f.option_count == 1
    assert any("孤立した決定ブロック" in r for r in f.reasons)
    assert any("実質エントリ数が1件" in r for r in f.reasons)


def test_well_discussed_cross_referenced_adr_is_not_flagged(tmp_path):
    cfg, docs, graph = _build(
        tmp_path,
        {
            "components/tier3_jit/jit_compiler.md": WELL_FORMED_ADR,
            "components/tier1_core/other_component.md": REFERENCING_DOC,
        },
    )
    findings = FakeDecisionDetector(cfg, repo_root=tmp_path).verify(docs, graph)
    assert all(f.keyword != "ADR_WellDiscussed" for f in findings)


def test_strawman_alternative_is_flagged(tmp_path):
    cfg, docs, graph = _build(tmp_path, {"components/tier3_jit/jit_compiler.md": STRAWMAN_ADR})
    findings = FakeDecisionDetector(cfg, repo_root=tmp_path).verify(docs, graph)
    f = _keyword(findings, "ADR_Strawman")
    assert f.option_count == 2
    assert any("藁人形" in r for r in f.reasons)


def test_heading_style_adr_without_keyword_uses_text_search_for_isolation(tmp_path):
    cfg, docs, graph = _build(tmp_path, {"components/tier1_core/os_scheduler.md": HEADING_STYLE})
    findings = FakeDecisionDetector(cfg, repo_root=tmp_path).verify(docs, graph)
    f = _keyword(findings, "ADR-SCHED-001")
    assert not f.is_bracket_keyword
    assert f.referenced_file_count == 0
    assert any("孤立した決定ブロック" in r for r in f.reasons)
    assert any("実質エントリ数が0件" in r for r in f.reasons)


def test_heading_style_adr_referenced_elsewhere_is_not_isolated(tmp_path):
    cfg, docs, graph = _build(
        tmp_path,
        {
            "components/tier1_core/os_scheduler.md": HEADING_STYLE,
            "components/tier1_core/other_component.md": "# Other\nSee ADR-SCHED-001 for the rationale.\n",
        },
    )
    findings = FakeDecisionDetector(cfg, repo_root=tmp_path).verify(docs, graph)
    f = _keyword(findings, "ADR-SCHED-001")
    assert f.referenced_file_count == 1
    assert not any("孤立した決定ブロック" in r for r in f.reasons)


def _git(repo_root, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def test_adr_block_touched_by_uncommitted_diff_is_flagged(tmp_path):
    cfg, docs, graph = _build(tmp_path, {"components/tier3_jit/jit_compiler.md": WELL_FORMED_ADR})
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    target = tmp_path / "docs" / "components" / "tier3_jit" / "jit_compiler.md"
    target.write_text(
        WELL_FORMED_ADR.replace("案3を採用する。", "案3を採用する（改訂）。"),
        encoding="utf-8",
    )
    cfg2, docs2, graph2 = _build(
        tmp_path,
        {"components/tier3_jit/jit_compiler.md": target.read_text(encoding="utf-8")},
    )
    findings = FakeDecisionDetector(cfg2, repo_root=tmp_path).verify(docs2, graph2)
    f = _keyword(findings, "ADR_WellDiscussed")
    assert f.in_working_diff
    assert any("未コミット差分" in r for r in f.reasons)


def test_no_git_repo_does_not_raise(tmp_path):
    cfg, docs, graph = _build(
        tmp_path, {"components/tier3_jit/jit_compiler.md": ISOLATED_SINGLE_OPTION}
    )
    findings = FakeDecisionDetector(cfg, repo_root=tmp_path).verify(docs, graph)
    f = _keyword(findings, "ADR_Lonely")
    assert not f.in_working_diff


def test_unlabeled_decision_in_working_diff_is_flagged(tmp_path):
    orig = "# Runtime\n## 1. Concept\nNormal description.\n"
    cfg, docs, graph = _build(tmp_path, {"components/tier2_runtime/runtime_engine.md": orig})
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    # Introduce a unilateral policy statement in diff without ADR
    modified = (
        "# Runtime\n## 1. Concept\n設計方針として、R3レジスタを独占的に内部使用に固定する。\n"
    )
    target = tmp_path / "docs" / "components" / "tier2_runtime" / "runtime_engine.md"
    target.write_text(modified, encoding="utf-8")
    cfg2, docs2, graph2 = _build(tmp_path, {"components/tier2_runtime/runtime_engine.md": modified})
    findings = FakeDecisionDetector(cfg2, repo_root=tmp_path).verify(docs2, graph2)
    prose_decisions = [f for f in findings if f.kind == "PROSE_DECISION"]
    assert len(prose_decisions) >= 1
    assert prose_decisions[0].in_working_diff
    assert any("コンポーネント内の地の文で" in r for r in prose_decisions[0].reasons)


def test_llm_verification_detects_unilateral_decision(tmp_path):
    doc_text = """# JIT
## 2. Register Layout
勝手な判断として、インタープリタと相談せずにR10をフラグ専用に固定することにする。
"""
    cfg, docs, graph = _build(tmp_path, {"components/tier3_jit/jit.md": doc_text})
    detector = FakeDecisionDetector(cfg, repo_root=tmp_path)
    mock_resp = """```json
{
  "is_problematic": true,
  "confidence": "HIGH",
  "issues": [
    {
      "category": "UNILATERAL_DECISION",
      "summary": "Arbitrary register pinning without trade-off analysis",
      "reason": "R10 is unilaterally pinned without consulting interpreter calling conventions.",
      "quote": "R10をフラグ専用に固定することにする"
    }
  ]
}
```"""

    with patch.object(FakeDecisionDetector, "_call_llm_backend", return_value=mock_resp):
        findings = detector.verify_with_llm(docs, backend_name="sakura")

    assert len(findings) == 1
    assert findings[0].kind == "LLM_FLAGGED"
    assert findings[0].confidence == "HIGH"
    assert any("UNILATERAL_DECISION" in r for r in findings[0].reasons)
