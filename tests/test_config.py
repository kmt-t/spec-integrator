from pathlib import Path

from spec_integrator.config import Config, TierConfig


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


def test_project_config_defaults_to_nimble_v3():
    project_root = Path(__file__).resolve().parents[3]
    cfg = Config.load(project_root / "spec-integrator.yaml")

    assert cfg.llm_judge.default_backend == "nimble"
    assert list(cfg.llm_judge.backends) == ["jev", "nimble"]
    assert cfg.llm_judge.backends["nimble"].endpoint == ("http://localhost:11434/v1/systemone")
    assert cfg.llm_judge.backends["nimble"].model == "nimble-v3:9b-q4_K_M"
    assert not cfg.llm_judge.backends["nimble"].requires_api_key
    assert cfg.llm_judge.backends["nimble"].context_window_tokens == 32768


def test_pysim_source_gate_config_matches_revised_rule_set() -> None:
    project_root = Path(__file__).resolve().parents[3]
    config = Config.load(project_root / "spec-integrator.yaml")
    group = config.source_verification.groups["python_pysim"]
    gate_checks = [check for check in group.checks if check.id == "gate_checks"]

    assert len(gate_checks) == 1
    gate = gate_checks[0]
    assert gate.rules == [
        "forbid_typing_any",
        "forbid_object_type",
        "forbid_non_none_union",
        "forbid_none_default_fill",
        "forbid_raise",
        "forbid_rtti",
        "forbid_test_backdoor",
        "forbid_required_nullable_arguments",
        "forbid_string_members",
        "dummy_pass",
        "todo_comment",
    ]
    assert gate.none_default_fill_exclude_paths == [
        "experiments/pysim/qa/**",
        "experiments/pysim/benchmarks/**",
    ]


def test_config_loads_prose_readability_thresholds(tmp_path: Path) -> None:
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(
        """prose_readability:
  max_sentence_characters: 120
  complex_sentence_min_characters: 80
  min_clause_links: 4
""",
        encoding="utf-8",
    )

    config = Config.load(cfg_file)

    assert config.prose_readability.max_sentence_characters == 120
    assert config.prose_readability.complex_sentence_min_characters == 80
    assert config.prose_readability.min_clause_links == 4


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


def test_config_loads_pysim_import_tiers(tmp_path: Path) -> None:
    cfg_file = tmp_path / "spec-integrator.yaml"
    cfg_file.write_text(
        """pysim_imports:
  root: experiments/pysim
  tiers:
    - tier: 1
      paths: [tier1_core/**/*.py]
    - tier: 2
      paths: [tier2_runtime/**/*.py]
""",
        encoding="utf-8",
    )

    cfg = Config.load(cfg_file)

    assert cfg.pysim_imports.root == "experiments/pysim"
    assert [(item.tier, item.paths) for item in cfg.pysim_imports.tiers] == [
        (1, ["tier1_core/**/*.py"]),
        (2, ["tier2_runtime/**/*.py"]),
    ]
