import pytest
from pathlib import Path
from spec_integrator.config import Config, TierConfig, KeywordRule


def test_tier_config_matching():
    tc = TierConfig(tier=1, name="Core", path_pattern="docs/components/tier1_*/**/*.md")
    assert tc.matches("docs/components/tier1_core/os_scheduler.md")
    assert tc.matches("docs/components/tier1_interface/ipc_router.md")
    assert not tc.matches("docs/components/tier2_runtime/interpreter.md")
    assert not tc.matches("docs/requires/req.md")


def test_brace_expansion():
    tc = TierConfig(tier="meta", name="Meta", path_pattern="docs/{architecture,plans}/**/*.md")
    assert tc.matches("docs/architecture/overview.md")
    assert tc.matches("docs/plans/roadmap.md")
    assert not tc.matches("docs/components/tier1_core/os.md")


def test_config_load_yaml(tmp_path):
    yaml_content = """version: "1.0"
project:
  name: "Test Project"
  docs_root: "docs"
tiers:
  - tier: 0
    name: "Reqs"
    path_pattern: "docs/requires/**/*.md"
keywords:
  local:
    pattern: "^[A-Z0-9_]+$"
    defined_in: "docs/requires/**/*.md"
"""
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(yaml_content, encoding="utf-8")

    cfg = Config.load(cfg_file)
    assert cfg.project.name == "Test Project"
    assert len(cfg.tiers) == 1
    assert cfg.get_tier_for_path("docs/requires/spec.md") == 0
    assert cfg.get_tier_for_path("docs/other/spec.md") is None
