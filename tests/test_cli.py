import pytest
import sys
from pathlib import Path
from spec_integrator.cli import cmd_init, cmd_check, cmd_graph


def test_cli_init_and_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # 1. Run init
    class ArgsInit:
        pass
    
    with pytest.raises(SystemExit) as exc_info:
        cmd_init(ArgsInit())
    assert exc_info.value.code == 0
    assert (tmp_path / "spec-integrator.yaml").exists()

    # 2. Setup mock docs
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    req_dir = docs_dir / "requires"
    req_dir.mkdir()
    (req_dir / "req.md").write_text("# Requirements\n## Feat {REQ_01}\nDef.", encoding="utf-8")

    comp_dir = docs_dir / "components" / "tier1_core"
    comp_dir.mkdir(parents=True)
    (comp_dir / "sched.md").write_text("# Sched\n## Design\nImplements {REQ_01}.", encoding="utf-8")

    # 3. Run check
    class ArgsCheck:
        config = "spec-integrator.yaml"
        report = "spec_report.md"
        graph_json = "graph.json"
        clean = True

    with pytest.raises(SystemExit) as exc_info:
        cmd_check(ArgsCheck())
    assert exc_info.value.code == 0
    assert (tmp_path / "spec_report.md").exists()
    assert (tmp_path / "graph.json").exists()
