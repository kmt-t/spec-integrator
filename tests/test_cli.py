import json
import pytest
import sys
from pathlib import Path
from spec_integrator.cli import cmd_init, cmd_check, cmd_graph


class ArgsCheck:
    config = "spec-integrator.yaml"
    report = "spec_report.md"
    graph_json = "graph.json"
    clean = True


def _scaffold(tmp_path):
    docs_dir = tmp_path / "docs"
    req_dir = docs_dir / "requires"
    req_dir.mkdir(parents=True)
    (req_dir / "req.md").write_text("# Requirements\n## Feat {REQ_01}\nDef.", encoding="utf-8")

    comp_dir = docs_dir / "components" / "tier1_core"
    comp_dir.mkdir(parents=True)
    (comp_dir / "sched.md").write_text("# Sched\n## Design\nImplements {REQ_01}.",
                                       encoding="utf-8")
    return docs_dir


def _write_clean_assessment(tmp_path, docs_dir):
    """A complete, up-to-date risk assessment demanding nothing."""
    from spec_integrator.config import Config
    from spec_integrator.parser import MarkdownParser

    cfg = Config.load(tmp_path / "spec-integrator.yaml")
    parser = MarkdownParser(cfg)
    hashes = {}
    sections = 0
    for md in sorted(docs_dir.rglob("*.md")):
        doc = parser.parse_file(md, docs_dir)
        hashes[doc.file_path] = doc.content_hash
        sections += len(doc.sections)

    out = tmp_path / "reports" / "doc_risk_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # A complete assessment covers every section; a partial one is not a clean bill.
    out.write_text(json.dumps({
        "total_evaluated": sections, "assessments": [], "doc_hashes": hashes
    }), encoding="utf-8")


def test_cli_init_creates_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass

    with pytest.raises(SystemExit) as exc_info:
        cmd_init(ArgsInit())
    assert exc_info.value.code == 0
    assert (tmp_path / "spec-integrator.yaml").exists()


def test_check_fails_when_the_risk_assessment_was_never_run(tmp_path, monkeypatch):
    """Skipping the step that decides *what* to verify must not yield a green build."""
    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass
    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    _scaffold(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        cmd_check(ArgsCheck())
    assert exc_info.value.code == 1

    report = (tmp_path / "spec_report.md").read_text(encoding="utf-8")
    assert "OBLIG-ASSESSMENT-MISSING" in report


def test_check_passes_with_a_complete_assessment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass
    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    docs_dir = _scaffold(tmp_path)
    _write_clean_assessment(tmp_path, docs_dir)

    with pytest.raises(SystemExit) as exc_info:
        cmd_check(ArgsCheck())
    assert exc_info.value.code == 0
    assert (tmp_path / "spec_report.md").exists()
    assert (tmp_path / "graph.json").exists()


def test_check_can_run_without_the_obligation_gate(tmp_path, monkeypatch):
    """Opting out must be an explicit, auditable choice recorded in the config."""
    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass
    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    cfg_path = tmp_path / "spec-integrator.yaml"
    cfg_path.write_text(cfg_path.read_text(encoding="utf-8")
                        + "\nobligation:\n  require_assessment: false\n  require_judge: false\n",
                        encoding="utf-8")
    _scaffold(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        cmd_check(ArgsCheck())
    assert exc_info.value.code == 0
