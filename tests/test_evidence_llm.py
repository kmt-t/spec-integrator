from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.judge.unified_reviewer import UnifiedReviewEngine


def test_claim_evidence_criterion_is_configured():
    """Verify that Claim-Evidence Substantiation is defined in project configuration."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    rule = next((r for r in config.llm_judge.checks if r.id == "claim_substantiation"), None)
    assert rule is not None
    prompt_text = rule.get_prompt_text(config.config_dir)
    assert "completed proof" in prompt_text
    assert "empirical measurement" in prompt_text


def test_claim_evidence_criterion_reaches_the_checksheet():
    """Verify that the criterion reaches the typed review checksheet."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    reviewer = UnifiedReviewEngine(config)

    checks = reviewer.get_effective_checks("link_pair", check_ids=["claim_substantiation"])
    assert len(checks) == 1
    sheet = reviewer.build_checksheet(
        "link_pair", "Test Link Pair", "Some section context", checks, ["test.md"]
    )
    question = sheet.questions["claim_substantiation"]
    assert "Claim-Evidence Substantiation" in question["instructions"]
    assert "completed proof" in question["instructions"]
    assert sheet.state["specification_content"] == "Some section context"
