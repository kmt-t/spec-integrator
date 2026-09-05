from pathlib import Path

from spec_integrator.anti_sabotage import AntiSabotageCheck, AntiSabotageContext, AntiSabotageRunner
from spec_integrator.anti_sabotage.checks import (
    BrokenAnchorCheck,
    BrokenLinkCheck,
    DeclaredEvidenceFileMissingCheck,
    DuplicateDefinitionCheck,
)
from spec_integrator.config import Config, KeywordRule
from spec_integrator.models import VerificationIssue
from spec_integrator.parser import MarkdownParser


def test_anti_sabotage_runner_basic(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    cfg = Config()
    cfg.config_dir = tmp_path

    # 空のドキュメントで実行
    ctx = AntiSabotageContext(
        documents=[],
        graph=None,
        docs_root=docs_dir,
        config=cfg,
    )
    runner = AntiSabotageRunner()
    issues = runner.run(ctx)
    assert isinstance(issues, list)


def test_custom_check_plugin():
    class DummyCheck(AntiSabotageCheck):
        rule_code = "CUSTOM-DUMMY"
        name = "テスト用ダミー"
        gate = "Format"

        def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
            return [
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path="dummy.md",
                    line=1,
                    rule_code=self.rule_code,
                    message="Dummy issue detected.",
                )
            ]

    runner = AntiSabotageRunner(checks=[DummyCheck()])
    ctx = AntiSabotageContext(
        documents=[],
        graph=None,
        docs_root=Path("."),
        config=Config(),
    )
    issues = runner.run(ctx)
    assert len(issues) == 1
    assert issues[0].rule_code == "CUSTOM-DUMMY"
    assert runner.run_gate("Formal", ctx) == []
    assert len(runner.run_gate("Format", ctx)) == 1


def test_broken_link_and_anchor_checks(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    cfg = Config()
    cfg.config_dir = tmp_path

    doc_a = docs_dir / "doc_a.md"
    doc_a.write_text(
        """# Doc A
Link to [NonExistent](non_existent.md).
Link to [Doc B Anchor](doc_b.md#missing-heading).
Valid Link to [Doc B](doc_b.md#valid-heading).
""",
        encoding="utf-8",
    )

    doc_b = docs_dir / "doc_b.md"
    doc_b.write_text(
        """# Doc B
## Valid Heading
Some content.
""",
        encoding="utf-8",
    )

    parser = MarkdownParser(cfg)
    docs = [parser.parse_file(doc_a, docs_dir), parser.parse_file(doc_b, docs_dir)]
    ctx = AntiSabotageContext(
        documents=docs,
        graph=None,
        docs_root=docs_dir,
        config=cfg,
    )

    runner = AntiSabotageRunner(checks=[BrokenLinkCheck(), BrokenAnchorCheck()])
    issues = runner.run(ctx)
    codes = [i.rule_code for i in issues]
    assert "FMT-BROKEN-LINK" in codes
    assert "FMT-BROKEN-ANCHOR" in codes


def test_evidence_and_consistency_checks(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    cfg = Config()
    cfg.config_dir = tmp_path
    cfg.keywords["local"] = KeywordRule(pattern="^REQ_[A-Z0-9_]+$", defined_in=r".*\.md")

    # 1. 存在しない証跡ファイル
    doc_evid = docs_dir / "evid.md"
    doc_evid.write_text(
        """# Evid Doc {VERIFY_FORMAL}
<!-- evidence: formal: missing_model.py -->
""",
        encoding="utf-8",
    )

    # 2. キーワードの重複定義
    doc_consist = docs_dir / "consist.md"
    doc_consist.write_text(
        """# Consist Doc
| {REQ_DUPLICATE} | First definition. |
| {REQ_DUPLICATE} | Second definition. |
""",
        encoding="utf-8",
    )

    parser = MarkdownParser(cfg)
    docs = [parser.parse_file(doc_evid, docs_dir), parser.parse_file(doc_consist, docs_dir)]
    ctx = AntiSabotageContext(
        documents=docs,
        graph=None,
        docs_root=docs_dir,
        config=cfg,
    )

    runner = AntiSabotageRunner(
        checks=[DeclaredEvidenceFileMissingCheck(), DuplicateDefinitionCheck()]
    )
    issues = runner.run(ctx)
    codes = [i.rule_code for i in issues]
    assert "EVID-DECLARED-FILE-MISSING" in codes
    assert "CONSIST-DUPLICATE-DEFINITION" in codes
