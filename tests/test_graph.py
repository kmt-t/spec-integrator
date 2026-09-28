from spec_integrator.config import Config
from spec_integrator.graph import DocGraphBuilder
from spec_integrator.parser import MarkdownParser


def test_doc_graph_builder(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    req_file = docs_dir / "requirement.md"
    req_file.write_text(
        """# Requirements
## Sched Requirement
<!-- definition: {REQ_SCHED} -->
{REQ_SCHED}: Definitions.
""",
        encoding="utf-8",
    )
    des_file = docs_dir / "design.md"
    des_file.write_text(
        """# Design
## Core Design
<!-- traceability: {REQ_SCHED} -->
See [Requirements](requirement.md#sched-requirement).
""",
        encoding="utf-8",
    )
    cfg = Config()
    parser = MarkdownParser(cfg)
    doc1 = parser.parse_file(req_file, docs_dir)
    doc2 = parser.parse_file(des_file, docs_dir)
    builder = DocGraphBuilder(cfg)
    graph = builder.build([doc1, doc2], docs_dir)
    assert "file:requirement.md" in graph.nodes
    assert "file:design.md" in graph.nodes
    assert "item:REQ_SCHED" in graph.nodes
    # Check defines vs refers_to
    defines_edges = [
        e for e in graph.edges if e.target == "item:REQ_SCHED" and e.relation == "defines"
    ]
    refers_edges = [
        e for e in graph.edges if e.target == "item:REQ_SCHED" and e.relation == "refers_to"
    ]
    assert len(defines_edges) == 1
    assert len(refers_edges) == 1
    # Check subgraphs
    subgraphs = graph.extract_item_subgraphs()
    assert len(subgraphs) == 1
    assert subgraphs[0]["item_id"] == "item:REQ_SCHED"
    assert len(subgraphs[0]["defined_in"]) == 1
    assert len(subgraphs[0]["referenced_in"]) == 1
    # Check Mermaid
    mermaid = graph.to_mermaid()
    assert "graph TD" in mermaid
    assert "REQ_SCHED" in mermaid


def test_doc_graph_uses_definition_and_traceability_comments(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    req_file = docs_dir / "requirement.md"
    req_file.write_text(
        """# Requirements
## Meaning
<!-- definition: {REQ_SCHED} -->
The requirement definition is {REQ_SCHED}.
## References
<!-- traceability: {REQ_SCHED} -->
""",
        encoding="utf-8",
    )
    design_file = docs_dir / "design.md"
    design_file.write_text(
        """# Design
## Runtime
<!-- traceability: {REQ_SCHED} -->
""",
        encoding="utf-8",
    )

    config = Config()
    parser = MarkdownParser(config)
    graph = DocGraphBuilder(config).build(
        [parser.parse_file(req_file, docs_dir), parser.parse_file(design_file, docs_dir)],
        docs_dir,
    )

    definitions = [
        edge
        for edge in graph.edges
        if edge.target == "item:REQ_SCHED" and edge.relation == "defines"
    ]
    references = [
        edge
        for edge in graph.edges
        if edge.target == "item:REQ_SCHED" and edge.relation == "refers_to"
    ]
    assert len(definitions) == 1
    assert definitions[0].source == "sec:requirement.md#Meaning"
    assert len(references) == 2


def test_keyword_index_location_does_not_create_a_definition_edge(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    indexed_file = docs_dir / "indexed.md"
    indexed_file.write_text(
        """# Indexed
## Mention
<!-- traceability: {REQ_INDEX_ONLY} -->
""",
        encoding="utf-8",
    )
    config = Config()
    parser = MarkdownParser(config)
    graph = DocGraphBuilder(config).build([parser.parse_file(indexed_file, docs_dir)], docs_dir)

    assert not any(
        edge.target == "item:REQ_INDEX_ONLY" and edge.relation == "defines" for edge in graph.edges
    )
    assert any(
        edge.target == "item:REQ_INDEX_ONLY" and edge.relation == "refers_to"
        for edge in graph.edges
    )
