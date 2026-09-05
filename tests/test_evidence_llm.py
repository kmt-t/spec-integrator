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
    assert "Unbacked Verification Claim" in prompt_text
    assert "Unsourced Metric / Measurement" in prompt_text


def test_claim_evidence_criterion_reaches_the_prompt():
    """Verify that the criterion actually reaches the review prompt."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    yaml_path = repo_root / "spec-integrator.yaml"
    config = Config.load(yaml_path)
    reviewer = UnifiedReviewEngine(config)

    checks = reviewer.get_effective_checks("cluster", check_ids=["claim_substantiation"])
    assert len(checks) == 1
    prompt = reviewer.assemble_prompt("cluster", "Test Island", "Some section context", checks)
    assert "Claim-Evidence Substantiation" in prompt
    assert "Unbacked Verification Claim" in prompt
    assert "=== OUTPUT FORMAT ===" in prompt
