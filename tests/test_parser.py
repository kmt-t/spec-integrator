import pytest
from pathlib import Path
from spec_integrator.config import Config, TierConfig, KeywordRule
from spec_integrator.parser import MarkdownParser


def test_markdown_parser(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    req_file = docs_dir / "req.md"
    req_file.write_text("""# System Requirements

## Scheduler Feature {REQ_SCHED_01}
This section defines cooperative scheduler. {VERIFY_FORMAL}
Link to [Design](design.md#details).

### Sub Item {REQ_SCHED_SUB}
Details here.
""", encoding="utf-8")

    cfg = Config()
    parser = MarkdownParser(cfg)

    doc = parser.parse_file(req_file, docs_dir)
    assert doc.file_path == "req.md"
    assert len(doc.sections) == 3
    assert "REQ_SCHED_01" in doc.all_keywords
    assert "REQ_SCHED_SUB" in doc.all_keywords
    assert "{VERIFY_FORMAL}" in doc.all_tags
    assert len(doc.all_links) == 1
    assert doc.all_links[0].target_path == "design.md"
    assert doc.all_links[0].target_anchor == "details"
