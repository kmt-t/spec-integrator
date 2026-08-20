import pytest
from pathlib import Path
from spec_integrator.config import Config, TierConfig, KeywordRule
from spec_integrator.parser import MarkdownParser
from spec_integrator.graph import DocGraphBuilder


def test_doc_graph_builder(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    
    req_file = docs_dir / "requirement.md"
    req_file.write_text("""# Requirements
## Sched Requirement {REQ_SCHED}
Definitions.
""", encoding="utf-8")

    des_file = docs_dir / "design.md"
    des_file.write_text("""# Design
## Core Design
Refines {REQ_SCHED}.
See [Requirements](requirement.md#sched-requirement).
""", encoding="utf-8")

    cfg = Config()
    cfg.keywords["local"] = KeywordRule(pattern="^REQ_[A-Z0-9_]+$", defined_in="requirement.md")

    parser = MarkdownParser(cfg)
    doc1 = parser.parse_file(req_file, docs_dir)
    doc2 = parser.parse_file(des_file, docs_dir)

    builder = DocGraphBuilder(cfg)
    graph = builder.build([doc1, doc2], docs_dir)

    assert "file:requirement.md" in graph.nodes
    assert "file:design.md" in graph.nodes
    assert "item:REQ_SCHED" in graph.nodes

    # Check defines vs refers_to
    defines_edges = [e for e in graph.edges if e.target == "item:REQ_SCHED" and e.relation == "defines"]
    refers_edges = [e for e in graph.edges if e.target == "item:REQ_SCHED" and e.relation == "refers_to"]
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
