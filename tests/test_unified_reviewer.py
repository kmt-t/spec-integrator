from __future__ import annotations

from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.graph import DocumentIsland, Edge, Graph, Node
from spec_integrator.judge.unified_reviewer import UnifiedReviewEngine
from spec_integrator.models import ParsedDocument, ParsedSection


def test_config_checks_loaded_from_project_yaml():
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    assert yaml_path.exists()
    config = Config.load(yaml_path)
    rules = config.llm_judge.checks
    assert len(rules) >= 9
    ids = {r.id for r in rules}
    assert "vertical_consistency" in ids
    assert "cross_doc_consistency" in ids
    assert "internal_consistency" in ids
    assert "numeric_agreement" in ids
    assert "natural_language_standards" in ids
    assert "reference_by_keyword_or_filename" in ids
    assert "rationale_over_history" in ids


def test_effective_checks_filtering(tmp_path: Path):
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(
        """version: "1.0"
project:
  name: "Test"
llm_judge:
  checks:
    - id: "rule1"
      name: "Rule 1"
      mode: ["single"]
      enabled: true
      prompt: "Check 1 prompt"
    - id: "rule2"
      name: "Rule 2"
      mode: ["cluster"]
      enabled: true
      prompt: "Check 2 prompt"
    - id: "rule3"
      name: "Rule 3"
      mode: ["single", "cluster"]
      enabled: false
      prompt: "Check 3 prompt"
""",
        encoding="utf-8",
    )
    config = Config.load(cfg_file)
    reviewer = UnifiedReviewEngine(config)

    single_checks = reviewer.get_effective_checks("single")
    assert len(single_checks) == 1
    assert single_checks[0].id == "rule1"

    cluster_checks = reviewer.get_effective_checks("cluster")
    assert len(cluster_checks) == 1
    assert cluster_checks[0].id == "rule2"

    specific_checks = reviewer.get_effective_checks("single", check_ids=["rule1"])
    assert len(specific_checks) == 1

    empty_checks = reviewer.get_effective_checks("single", check_ids=["rule2"])
    assert len(empty_checks) == 0


def test_graph_extract_document_islands():
    graph = Graph()
    graph.add_node(Node(id="file:doc_a.md", label="doc_a.md", type="file", file_path="doc_a.md"))
    graph.add_node(Node(id="file:doc_b.md", label="doc_b.md", type="file", file_path="doc_b.md"))
    graph.add_node(Node(id="file:doc_c.md", label="doc_c.md", type="file", file_path="doc_c.md"))

    graph.add_node(
        Node(id="sec:doc_a.md#intro", label="Intro", type="section", file_path="doc_a.md")
    )
    graph.add_node(
        Node(id="sec:doc_b.md#design", label="Design", type="section", file_path="doc_b.md")
    )

    # doc_a links to doc_b
    graph.add_edge(Edge(source="sec:doc_a.md#intro", target="file:doc_b.md", relation="links_to"))

    islands = graph.extract_document_islands(min_size=1)
    # doc_a and doc_b should be in one island of size 2, doc_c in an island of size 1
    assert len(islands) == 2
    island_files = [set(isl.file_paths) for isl in islands]
    assert {"doc_a.md", "doc_b.md"} in island_files
    assert {"doc_c.md"} in island_files


def test_review_single_document_dry_run(tmp_path: Path):
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(
        """version: "1.0"
llm_judge:
  checks:
    - id: "test_rule"
      name: "Test Rule"
      mode: ["single"]
      enabled: true
      prompt: "Test prompt"
""",
        encoding="utf-8",
    )
    config = Config.load(cfg_file)
    reviewer = UnifiedReviewEngine(config)

    doc_path = tmp_path / "test.md"
    doc_path.write_text("# Test", encoding="utf-8")
    doc = ParsedDocument(
        file_path="components/tier1_core/test.md",
        full_path=doc_path,
        tier=1,
        component="test",
        content="# Test Component\n\n## Section 1\nSome description",
        content_hash="abc",
        sections=[
            ParsedSection(
                section_id="sec:components/tier1_core/test.md#Section 1",
                file_path="components/tier1_core/test.md",
                heading="Section 1",
                level=2,
                line_start=3,
                line_end=4,
                body_text="Some description",
            )
        ],
    )

    res = reviewer.review_single_document(doc, dry_run=True)
    assert res.status == "PASS"
    assert "Dry Run" in res.summary


def test_review_document_island_dry_run(tmp_path: Path):
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(
        """version: "1.0"
llm_judge:
  checks:
    - id: "test_cluster_rule"
      name: "Test Cluster Rule"
      mode: ["cluster"]
      enabled: true
      prompt: "Test cluster prompt"
""",
        encoding="utf-8",
    )
    config = Config.load(cfg_file)
    reviewer = UnifiedReviewEngine(config)

    p_a = tmp_path / "doc_a.md"
    p_a.write_text("# Doc A", encoding="utf-8")
    doc_a = ParsedDocument(
        file_path="components/tier1_core/doc_a.md",
        full_path=p_a,
        tier=1,
        component="doc_a",
        content="# Doc A\n\n## Sec A\nText A",
        content_hash="hash_a",
        sections=[
            ParsedSection(
                section_id="sec:components/tier1_core/doc_a.md#Sec A",
                file_path="components/tier1_core/doc_a.md",
                heading="Sec A",
                level=2,
                line_start=3,
                line_end=4,
                body_text="Text A",
            )
        ],
    )
    p_b = tmp_path / "doc_b.md"
    p_b.write_text("# Doc B", encoding="utf-8")
    doc_b = ParsedDocument(
        file_path="components/tier1_core/doc_b.md",
        full_path=p_b,
        tier=1,
        component="doc_b",
        content="# Doc B\n\n## Sec B\nText B",
        content_hash="hash_b",
        sections=[
            ParsedSection(
                section_id="sec:components/tier1_core/doc_b.md#Sec B",
                file_path="components/tier1_core/doc_b.md",
                heading="Sec B",
                level=2,
                line_start=3,
                line_end=4,
                body_text="Text B",
            )
        ],
    )

    island = DocumentIsland(
        island_id="island_01",
        name="Test Island",
        file_paths=["components/tier1_core/doc_a.md", "components/tier1_core/doc_b.md"],
        section_ids=[
            "sec:components/tier1_core/doc_a.md#Sec A",
            "sec:components/tier1_core/doc_b.md#Sec B",
        ],
        keywords=["TestKW"],
        total_sections=2,
        total_docs=2,
    )

    res = reviewer.review_document_island(island, [doc_a, doc_b], dry_run=True)
    assert res.status == "PASS"
    assert "Dry Run" in res.summary


def test_review_mock_backend(tmp_path: Path):
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(
        """version: "1.0"
llm_judge:
  checks:
    - id: "test_rule"
      name: "Test Rule"
      mode: ["single"]
      enabled: true
      prompt: "Test prompt"
""",
        encoding="utf-8",
    )
    config = Config.load(cfg_file)
    reviewer = UnifiedReviewEngine(config)

    p_test = tmp_path / "test.md"
    p_test.write_text("# Test", encoding="utf-8")
    doc = ParsedDocument(
        file_path="components/tier1_core/test.md",
        full_path=p_test,
        tier=1,
        component="test",
        content="# Test Component\n## Sec\nText",
        content_hash="hash_t",
        sections=[
            ParsedSection(
                section_id="sec:components/tier1_core/test.md#Sec",
                file_path="components/tier1_core/test.md",
                heading="Sec",
                level=2,
                line_start=2,
                line_end=3,
                body_text="Text",
            )
        ],
    )

    res = reviewer.review_single_document(doc, backend="mock")
    assert res.status == "PASS"
    assert "Mock evaluation passed" in res.summary
