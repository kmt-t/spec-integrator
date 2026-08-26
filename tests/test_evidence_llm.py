from spec_integrator.config import Config
from spec_integrator.judge.semantic_judge import JUDGE_PROMPT_TEMPLATE, SemanticJudge


def _capture_prompt(judge: SemanticJudge) -> list[str]:
    captured: list[str] = []

    def fake_call(prompt: str, model: str | None):
        captured.append(prompt)
        return '{"status": "PASS", "summary": "ok", "issues": []}'

    judge._call_sakura = fake_call
    return captured


def _evaluate_empty_subgraph(judge: SemanticJudge):
    sg = {"item_id": "item:{K}", "item_label": "{K}", "defined_in": [], "referenced_in": []}
    return judge._evaluate_single_subgraph(sg, [], backend="sakura", model=None)


def test_claim_evidence_criterion_is_always_included_in_prompt_template():
    """Verify that Claim-Evidence Substantiation is an inherent part of JUDGE_PROMPT_TEMPLATE."""
    assert "Claim-Evidence Substantiation & Unbacked Assertions" in JUDGE_PROMPT_TEMPLATE
    assert "Unbacked Verification Claim" in JUDGE_PROMPT_TEMPLATE
    assert "Unsourced Metric / Measurement" in JUDGE_PROMPT_TEMPLATE


def test_claim_evidence_criterion_reaches_the_prompt():
    """Verify that the criterion actually reaches the LLM backend call."""
    config = Config()
    judge = SemanticJudge(config)
    captured = _capture_prompt(judge)

    _evaluate_empty_subgraph(judge)

    assert len(captured) == 1
    assert "Claim-Evidence Substantiation & Unbacked Assertions" in captured[0]
    assert "Unbacked Verification Claim" in captured[0]
    assert "6. Redundancy & Duplication Audit" in captured[0]
    assert "=== OUTPUT FORMAT ===" in captured[0]
