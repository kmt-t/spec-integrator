import pytest
from spec_integrator.cli import (
    cmd_check_doc,
    cmd_check_src,
    cmd_format_doc,
    cmd_format_src,
    cmd_init,
)
from spec_integrator.db import DocAuditDB


class ArgsCheckDoc:
    config = "spec-integrator.yaml"
    report = "spec_report.md"
    clean = True
    files = None


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


def test_check_doc_fails_when_the_risk_assessment_was_never_run(tmp_path, monkeypatch):
    """Skipping the step that decides *what* to verify must not yield a green build."""
    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass

    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    _scaffold(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        cmd_check_doc(ArgsCheckDoc())
    assert exc_info.value.code == 1
    report = (tmp_path / "spec_report.md").read_text(encoding="utf-8")
    assert "OBLIG-ASSESSMENT-MISSING" in report


def test_check_doc_passes_with_a_complete_assessment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass

    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    docs_dir = _scaffold(tmp_path)
    _write_clean_assessment(tmp_path, docs_dir)
    with pytest.raises(SystemExit) as exc_info:
        cmd_check_doc(ArgsCheckDoc())
    assert exc_info.value.code == 0
    assert (tmp_path / "spec_report.md").exists()


def test_check_doc_can_run_without_the_obligation_gate(tmp_path, monkeypatch):
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
        cmd_check_doc(ArgsCheckDoc())
    assert exc_info.value.code == 0


def test_cli_build(tmp_path, monkeypatch):
    from spec_integrator.cli import cmd_build

    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass

    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    _scaffold(tmp_path)

    class ArgsBuild:
        config = "spec-integrator.yaml"
        clean = True
        files = None

    with pytest.raises(SystemExit) as exc_info:
        cmd_build(ArgsBuild())
    assert exc_info.value.code == 0

    from spec_integrator.config import Config

    cfg = Config.load(tmp_path / "spec-integrator.yaml")
    db = DocAuditDB(cfg.get_db_path())
    terms = db.get_all_term_keywords()
    assert len(terms) > 0
    db.close()


def test_cli_format_doc(monkeypatch, tmp_path):
    class ArgsFormatDoc:
        config = "spec-integrator.yaml"
        files = None

    monkeypatch.chdir(tmp_path)
    class ArgsInit:
        pass
    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    with pytest.raises(SystemExit) as exc_info:
        cmd_format_doc(ArgsFormatDoc())
    assert exc_info.value.code == 0


def test_cli_format_src(monkeypatch, tmp_path):
    import subprocess

    class ArgsFormatSrc:
        config = "spec-integrator.yaml"
        group = "all"
        files = None

    monkeypatch.chdir(tmp_path)
    class ArgsInit:
        pass
    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    with pytest.raises(SystemExit) as exc_info:
        cmd_format_src(ArgsFormatSrc())
    assert exc_info.value.code == 0


def test_cli_check_src(monkeypatch, tmp_path):
    class ArgsCheckSrc:
        config = "spec-integrator.yaml"
        group = "all"
        files = None

    monkeypatch.chdir(tmp_path)
    class ArgsInit:
        pass
    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    with pytest.raises(SystemExit) as exc_info:
        cmd_check_src(ArgsCheckSrc())
    assert exc_info.value.code == 0


def test_cli_list_checks(tmp_path, monkeypatch):
    from spec_integrator.cli import cmd_llm_keyword_review, cmd_llm_single_review

    monkeypatch.chdir(tmp_path)

    class ArgsInit:
        pass

    with pytest.raises(SystemExit):
        cmd_init(ArgsInit())

    class ArgsList:
        config = "spec-integrator.yaml"
        list_checks = True

    with pytest.raises(SystemExit) as exc_info:
        cmd_llm_single_review(ArgsList())
    assert exc_info.value.code == 0

    with pytest.raises(SystemExit) as exc_info:
        cmd_llm_keyword_review(ArgsList())
    assert exc_info.value.code == 0


def test_cli_subparsers_args():
    import argparse

    from spec_integrator.cli import _SUBPARSER_BUILDERS

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    for builder in _SUBPARSER_BUILDERS:
        builder(subparsers)

    # Test that each subcommand parses without error
    args_build = parser.parse_args(["build", "--clean", "doc1.md", "doc2.md"])
    assert args_build.subcommand == "build"
    assert args_build.clean is True
    assert args_build.files == ["doc1.md", "doc2.md"]

    args_format_doc = parser.parse_args(["format-doc", "test.md"])
    assert args_format_doc.subcommand == "format-doc"
    assert args_format_doc.files == ["test.md"]

    args_check_doc = parser.parse_args(["check-doc", "-r", "custom_report.md"])
    assert args_check_doc.subcommand == "check-doc"
    assert args_check_doc.report == "custom_report.md"

    args_format_src = parser.parse_args(["format-src", "-g", "python", "file.py"])
    assert args_format_src.subcommand == "format-src"
    assert args_format_src.group == "python"
    assert args_format_src.files == ["file.py"]

    args_check_src = parser.parse_args(["check-src", "-g", "cpp", "file.cxx"])
    assert args_check_src.subcommand == "check-src"
    assert args_check_src.group == "cpp"
    assert args_check_src.files == ["file.cxx"]

    args_risk = parser.parse_args(["risk", "--max-keywords", "5", "-a"])
    assert args_risk.subcommand == "risk"
    assert args_risk.max_keywords == 5
    assert args_risk.exhaustive is True

    args_word = parser.parse_args(["llm-word", "--quick", "--threshold", "0.85"])
    assert args_word.subcommand == "llm-word"
    assert args_word.quick is True
    assert args_word.threshold == 0.85

    args_single = parser.parse_args(["llm-single-review", "--all", "--dry-run"])
    assert args_single.subcommand == "llm-single-review"
    assert args_single.all is True
    assert args_single.dry_run is True

    args_keyword = parser.parse_args(["llm-keyword-review", "--keyword", "JIT", "--dry-run"])
    assert args_keyword.subcommand == "llm-keyword-review"
    assert args_keyword.keyword == "JIT"
    assert args_keyword.dry_run is True
