import pytest
from spec_integrator.cli import cmd_check, cmd_init
from spec_integrator.db import DocAuditDB


class ArgsCheck:
    config = "spec-integrator.yaml"
    report = "spec_report.md"
    clean = True


def _scaffold(tmp_path):
    docs_dir = tmp_path / "docs"
    req_dir = docs_dir / "requires"
    req_dir.mkdir(parents=True)
    (req_dir / "req.md").write_text("# Requirements\n## Feat {REQ_01}\nDef.", encoding="utf-8")
    comp_dir = docs_dir / "components" / "tier1_core"
    comp_dir.mkdir(parents=True)
    (comp_dir / "sched.md").write_text("# Sched\n## Design\nImplements {REQ_01}.", encoding="utf-8")
    return docs_dir


def _write_clean_assessment(tmp_path, docs_dir):
    """A complete, up-to-date risk assessment demanding nothing."""
    from spec_integrator.config import Config
    from spec_integrator.parser import MarkdownParser

    cfg = Config.load(tmp_path / "spec-integrator.yaml")
    parser = MarkdownParser(cfg)
    hashes = {}
    for md in sorted(docs_dir.rglob("*.md")):
        doc = parser.parse_file(md, docs_dir)
        hashes[doc.file_path] = doc.content_hash

    # A complete assessment covers every keyword; a partial one is not a clean
    # bill. `{REQ_01}` is the one keyword `_scaffold`'s documents cite, so it
    # must be represented here (at low risk, so it demands nothing) for the
    # assessment to be "complete" against the graph's own keyword count.
    db = DocAuditDB(cfg.get_db_path())
    db.replace_risk_assessments(
        [
            {
                "item_id": "item:REQ_01",
                "keyword": "REQ_01",
                "file_path": "requires/req.md",
                "risk_score": 1,
                "covered_files": ["requires/req.md"],
            }
        ],
        "sakura",
    )
    db.set_assessed_doc_hashes("risk_assessment", hashes)
    db.commit()
    db.close()


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


def test_check_can_run_without_the_obligation_gate(tmp_path, monkeypatch):
    """Opting out must be an explicit, auditable choice recorded in the config."""
    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass

    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    cfg_path = tmp_path / "spec-integrator.yaml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + "\nobligation:\n  require_assessment: false\n  require_judge: false\n",
        encoding="utf-8",
    )
    _scaffold(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        cmd_check(ArgsCheck())
    assert exc_info.value.code == 0
