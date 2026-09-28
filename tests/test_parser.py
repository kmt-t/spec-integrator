from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser


def test_markdown_parser(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    req_file = docs_dir / "req.md"
    req_file.write_text(
        """# System Requirements
## Scheduler Feature {REQ_SCHED_01}
This section defines cooperative scheduler. {VERIFY_FORMAL}
Link to [Design](design.md#details).
### Sub Item {REQ_SCHED_SUB}
Details here.
""",
        encoding="utf-8",
    )
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


def test_keyword_definitions_and_references_have_distinct_source_syntax(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    source = docs_dir / "source.md"
    source.write_text(
        """# Keywords
## Source
The definition is {REQ_DEFINED}.
<!-- traceability: {REQ_REFERENCED} -->
```text
{REQ_FENCED}
```
""",
        encoding="utf-8",
    )

    parsed = MarkdownParser(Config()).parse_file(source, docs_dir)
    section = parsed.sections[1]

    assert section.definition_keywords == ["REQ_DEFINED"]
    assert section.reference_keywords == ["REQ_REFERENCED"]
    assert set(parsed.all_keywords) == {"REQ_DEFINED", "REQ_REFERENCED"}
