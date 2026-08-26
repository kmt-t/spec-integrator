from spec_integrator.config import Config
from spec_integrator.judge.semantic_judge import CLAIM_EVIDENCE_CRITERION, SemanticJudge


def _capture_prompt(judge: SemanticJudge) -> list[str]:
    """Monkeypatches _call_sakura to record the prompt it was called with,
    instead of asserting against the module-level template string directly --
    that would pass even if llm_substantiation_audit were never read anywhere,
    since the template text exists regardless of whether it reaches the LLM."""
    captured: list[str] = []

    def fake_call(prompt: str, model: str | None):
        captured.append(prompt)
        return '{"status": "PASS", "summary": "ok", "issues": []}'

    judge._call_sakura = fake_call
    return captured


def _evaluate_empty_subgraph(judge: SemanticJudge):
    sg = {"item_id": "item:{K}", "item_label": "{K}", "defined_in": [], "referenced_in": []}
    return judge._evaluate_single_subgraph(sg, [], backend="sakura", model=None)


def test_evidence_config_llm_flag_defaults_enabled():
    cfg = Config()
    assert hasattr(cfg.evidence, "llm_substantiation_audit")
    assert cfg.evidence.llm_substantiation_audit is True


def test_claim_evidence_criterion_reaches_the_prompt_when_enabled():
    """The flag being True must actually cause the criterion to appear in what
    gets sent to the backend, not just exist as an always-included template
    fragment."""
    config = Config()
    assert config.evidence.llm_substantiation_audit is True
    judge = SemanticJudge(config)
    captured = _capture_prompt(judge)

    _evaluate_empty_subgraph(judge)

    assert len(captured) == 1
    assert "Claim-Evidence Substantiation & Unbacked Assertions" in captured[0]
    assert "Unbacked Verification Claim" in captured[0]


def test_claim_evidence_criterion_absent_from_prompt_when_disabled():
    """The flag must be a real toggle: turning it off must remove the criterion
    from the actual prompt sent to the backend, not just leave a dead config
    field that nothing reads."""
    config = Config()
    config.evidence.llm_substantiation_audit = False
    judge = SemanticJudge(config)
    captured = _capture_prompt(judge)

    _evaluate_empty_subgraph(judge)

    assert len(captured) == 1
    assert "Claim-Evidence Substantiation & Unbacked Assertions" not in captured[0]
    assert "Unbacked Verification Claim" not in captured[0]
    # Disabling the extra criterion must not remove the base criteria around it.
    assert "5. Clarity" in captured[0]
    assert "=== OUTPUT FORMAT ===" in captured[0]


def test_claim_evidence_criterion_constant_matches_prompt_wording():
    """Guards against the prompt fragment and the standalone constant drifting
    apart -- the constant is what test_claim_evidence_criterion_reaches_the_prompt_when_enabled
    checks for a substring of, but nothing otherwise ties them together."""
    assert "Claim-Evidence Substantiation & Unbacked Assertions" in CLAIM_EVIDENCE_CRITERION
    assert "Unsourced Metric / Measurement" in CLAIM_EVIDENCE_CRITERION
