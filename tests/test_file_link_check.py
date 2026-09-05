# -*- coding: utf-8 -*-
import pytest
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.anti_sabotage.base import AntiSabotageContext
from spec_integrator.anti_sabotage.runner import AntiSabotageRunner
from spec_integrator.anti_sabotage.checks.fmt_file_link import FileLinkFormatCheck


def test_file_link_format_valid(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    target_file = docs_dir / "target.md"
    target_file.write_text("# Target\n", encoding="utf-8")

    cfg = Config()
    cfg.config_dir = tmp_path

    doc_a = docs_dir / "doc_a.md"
    doc_a.write_text(
        """# Doc A
Valid link: [target.md](docs/target.md)
Valid code link: [`target.md`](docs/target.md)
Valid anchor: [target.md#heading](docs/target.md#heading)
""",
        encoding="utf-8",
    )

    parser = MarkdownParser(cfg)
    docs = [parser.parse_file(doc_a, docs_dir), parser.parse_file(target_file, docs_dir)]
    ctx = AntiSabotageContext(
        documents=docs,
        graph=None,
        docs_root=docs_dir,
        config=cfg,
    )

    runner = AntiSabotageRunner(checks=[FileLinkFormatCheck()])
    issues = runner.run(ctx)
    assert issues == []


def test_file_link_format_invalid_links(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    target_file = docs_dir / "target.md"
    target_file.write_text("# Target\n", encoding="utf-8")

    cfg = Config()
    cfg.config_dir = tmp_path

    doc_bad = docs_dir / "doc_bad.md"
    doc_bad.write_text(
        """# Doc Bad
Relative dot-dot: [target.md](../docs/target.md)
Wrong label: [Description Here](docs/target.md)
Absolute file: [target.md](file:///x/docs/target.md)
""",
        encoding="utf-8",
    )

    parser = MarkdownParser(cfg)
    docs = [parser.parse_file(doc_bad, docs_dir), parser.parse_file(target_file, docs_dir)]
    ctx = AntiSabotageContext(
        documents=docs,
        graph=None,
        docs_root=docs_dir,
        config=cfg,
    )

    runner = AntiSabotageRunner(checks=[FileLinkFormatCheck()])
    issues = runner.run(ctx)
    codes = [i.rule_code for i in issues]
    assert len(issues) == 3
    assert all(c == "FMT-FILE-LINK-FORMAT" for c in codes)


def test_unlinked_file_path_detected(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    target_file = docs_dir / "target.md"
    target_file.write_text("# Target\n", encoding="utf-8")

    cfg = Config()
    cfg.config_dir = tmp_path

    doc_unlinked = docs_dir / "doc_unlinked.md"
    doc_unlinked.write_text(
        """# Doc Unlinked
Here is an unlinked path: `docs/target.md` should be a link.
And raw: docs/target.md is also not linked.
```python
# In code block, docs/target.md is ignored
print("docs/target.md")
```
<!-- In comment, docs/target.md is ignored -->
""",
        encoding="utf-8",
    )

    parser = MarkdownParser(cfg)
    docs = [parser.parse_file(doc_unlinked, docs_dir), parser.parse_file(target_file, docs_dir)]
    ctx = AntiSabotageContext(
        documents=docs,
        graph=None,
        docs_root=docs_dir,
        config=cfg,
    )

    runner = AntiSabotageRunner(checks=[FileLinkFormatCheck()])
    issues = runner.run(ctx)
    codes = [i.rule_code for i in issues]
    assert len(issues) >= 1
    assert any("Unlinked file path 'docs/target.md'" in i.message for i in issues)
