import pytest
from spec_integrator.config import Config
from spec_integrator.judge.semantic_judge import JUDGE_PROMPT_TEMPLATE, SemanticJudge


def test_evidence_config_llm_flag():
    """Verify that EvidenceConfig has llm_substantiation_audit enabled by default."""
    cfg = Config()
    assert hasattr(cfg.evidence, "llm_substantiation_audit")
    assert cfg.evidence.llm_substantiation_audit is True


def test_judge_prompt_contains_claim_evidence_criterion():
    """Verify that JUDGE_PROMPT_TEMPLATE includes the Claim-Evidence Substantiation criterion."""
    assert "Claim-Evidence Substantiation & Unbacked Assertions" in JUDGE_PROMPT_TEMPLATE
    assert "Unbacked Verification Claim" in JUDGE_PROMPT_TEMPLATE
    assert "Unsourced Metric / Measurement" in JUDGE_PROMPT_TEMPLATE
    assert "formally verified" in JUDGE_PROMPT_TEMPLATE


def test_judge_prompt_construction_with_evidence():
    """Verify prompt formatting with definition and referencing texts."""
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        item_label="{MemoryBoundaryCheck}",
        definition_texts="### Definition\nMust enforce linear memory bounds.",
        referencing_texts="### Design\nJIT compiles memory access with FastAddressCheck."
    )
    assert "Target Keyword/Requirement ID: {MemoryBoundaryCheck}" in prompt
    assert "Claim-Evidence Substantiation & Unbacked Assertions" in prompt
