import pytest
from pathlib import Path
from spec_integrator.config import Config, TierConfig, KeywordRule


def test_tier_config_regex_matching():
    tc = TierConfig(tier=1, name="Core", path_pattern=r"components/tier1_.*\.md")
    assert tc.matches("docs/components/tier1_core/os_scheduler.md")
    assert tc.matches("components/tier1_interface/ipc_router.md")
    assert not tc.matches("docs/components/tier2_runtime/interpreter.md")
    assert not tc.matches("docs/requires/req.md")


def test_meta_regex_matching():
    tc = TierConfig(tier="meta", name="Meta", path_pattern=r"(architecture|plans)/.*\.md")
    assert tc.matches("docs/architecture/overview.md")
    assert tc.matches("plans/roadmap.md")
    assert not tc.matches("docs/components/tier1_core/os.md")


def test_config_load_yaml(tmp_path):
    yaml_content = """version: "1.0"
project:
  name: "Test Project"
  docs_root: "docs"
tiers:
  - tier: 0
    name: "Reqs"
    path_pattern: 'requires/.*\\.md'
keywords:
  local:
    pattern: '^[A-Z0-9_]+$'
    defined_in: 'requires/.*\\.md'
"""
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(yaml_content, encoding="utf-8")

    cfg = Config.load(cfg_file)
    assert cfg.project.name == "Test Project"
    assert len(cfg.tiers) == 1
    assert cfg.get_tier_for_path("docs/requires/spec.md") == 0
    assert cfg.get_tier_for_path("docs/other/spec.md") is None


def test_config_exclude_patterns(tmp_path):
    yaml_content = """version: "1.0"
project:
  name: "Test Exclude"
  docs_root: "docs"
  exclude_patterns:
    - "**/FORMAT.md"
    - "templates/*.md"
"""
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(yaml_content, encoding="utf-8")

    cfg = Config.load(cfg_file)
    assert cfg.is_excluded("docs/components/FORMAT.md")
    assert cfg.is_excluded("architecture/FORMAT.md")
    assert cfg.is_excluded("FORMAT.md")
    assert cfg.is_excluded("templates/custom_spec.md")
    assert not cfg.is_excluded("components/tier1_core/os_coos.md")
