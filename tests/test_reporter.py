import pytest
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.graph import DocGraphBuilder
from spec_integrator.reporter import Reporter
from spec_integrator.verifier.static import VerificationIssue
from spec_integrator.verifier.formal import FormalModelResult
from spec_integrator.verifier.wit import WITFileResult


def test_reporter(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    f1 = docs_dir / "req.md"
    f1.write_text("# Requirements\n## Feature {REQ_01}", encoding="utf-8")

    cfg = Config()
    parser = MarkdownParser(cfg)
    doc = parser.parse_file(f1, docs_dir)
    builder = DocGraphBuilder(cfg)
    graph = builder.build([doc], docs_dir)

    issues = [
        VerificationIssue(gate="Format", severity="ERROR", file_path="req.md", line=2, rule_code="FMT-01", message="Test issue")
    ]
    formal_res = [
        FormalModelResult(component="tier1_core", model_file="formal/m.py", status="PASS", details="OK")
    ]
    wit_res = [
        WITFileResult(component="tier1_interface", wit_file="wit/api.wit", status="PASS", details="Valid WIT")
    ]

    out_md = tmp_path / "report.md"
    out_json = tmp_path / "graph.json"

    reporter = Reporter(cfg)
    report_text = reporter.generate_markdown_report([doc], graph, issues, formal_res, wit_res, out_md)
    reporter.export_graph_json(graph, out_json)

    assert out_md.exists()
    assert out_json.exists()
    assert "Spec Verification Report" in report_text
    assert "FMT-01" in report_text
    assert "Formal Verification Results" in report_text
    assert "WIT Interface Verification Results" in report_text
