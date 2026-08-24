from pathlib import Path

from spec_integrator.config import Config, RiskAssessmentConfig, HeuristicConfig, WaiverRule
from spec_integrator.parser import ParsedDocument, ParsedSection
from spec_integrator.judge.risk_assessor import (
    RiskAssessor, SectionRiskAssessment, RiskAssessmentReport, _keyword_matches,
)


def test_risk_assessment_report_markdown():
    report = RiskAssessmentReport(
        assessments=[
            SectionRiskAssessment(
                section_id="sec:os_coos.md#CSP通信",
                file_path="components/tier1_core/os_coos.md",
                heading="CSP通信",
                tier=1,
                complexity_score=4,
                risk_score=5,
                formal_needed=True,
                recommended_verification="pyModelChecking",
                suggested_tags=["{VERIFY_FORMAL}"],
                risk_factors=["Deadlock risk during handoff", "Race conditions in channel queues"],
                summary="High concurrency complexity in CSP handoff."
            ),
            SectionRiskAssessment(
                section_id="sec:system_config.md#静的定数",
                file_path="components/tier1_core/system_config.md",
                heading="静的定数",
                tier=1,
                complexity_score=1,
                risk_score=1,
                formal_needed=False,
                recommended_verification="Static",
                suggested_tags=[],
                risk_factors=[],
                summary="Simple declarative constants."
            )
        ],
        total_evaluated=2,
        formal_candidates_count=1,
        llm_candidates_count=0
    )

    md = report.to_markdown()
    assert "# Fireball 設計複雑度 & リスク評価レポート" in md
    assert "形式検証 (pyModelChecking) 推奨セクション" in md
    assert "`components/tier1_core/os_coos.md`" in md
    assert "`{VERIFY_FORMAL}`" in md


def test_heuristic_config_has_no_hardcoded_word_lists():
    """formal_triggers/llm_triggers/non_formal_path_patterns are project
    vocabulary, not tool behavior -- spec-integrator.yaml must be their only
    source, or the Python copy silently drifts from the YAML the next time
    only one of them gets edited."""
    defaults = HeuristicConfig()
    assert defaults.formal_triggers == []
    assert defaults.llm_triggers == []
    assert defaults.non_formal_path_patterns == []


def test_keyword_matches_respects_word_boundaries():
    assert _keyword_matches("mpu", "the mpu region is w^x protected") is True
    assert _keyword_matches("mpu", "an unrelated word like ampule") is False, \
        "a substring hit inside a longer token must not count as a match"
    assert _keyword_matches("trade-off", "this is a classic trade-off between x and y") is True
    assert _keyword_matches("trade-off", "tradeoffs without the hyphen do not count") is False


def test_keyword_matches_does_not_fire_on_a_tag_citation_alone():
    """A section that merely cites {CSPCommunication} as a traceability tag
    -- without its prose ever discussing CSP semantics -- must not be
    treated as a formal-verification trigger just because the tag name
    happens to contain the trigger word as a substring."""
    keywords_lower = "cspcommunication otherkeyword"
    assert _keyword_matches("csp", keywords_lower) is False
    assert _keyword_matches("csp", "the csp rendezvous blocks both sides") is True


def test_keyword_matches_falls_back_to_substring_for_cjk_terms():
    """CJK text has no whitespace word boundaries to anchor a \\b-style
    check on without a real tokenizer, so Japanese terms keep matching as a
    substring -- this is a known, accepted limitation, not a regression."""
    assert _keyword_matches("根拠", "その根拠は仕様書に明記されている") is True


def test_call_heuristic_does_not_flag_formal_from_a_bare_tag_citation():
    """End-to-end: the same false positive, exercised through _call_heuristic
    rather than the helper directly."""
    cfg = Config()
    cfg.risk_assessment = RiskAssessmentConfig(
        heuristic=HeuristicConfig(formal_triggers=["csp"], llm_triggers=[])
    )
    assessor = RiskAssessor(cfg)

    doc = ParsedDocument(
        file_path="components/tier1_core/os_coos.md",
        full_path=Path("components/tier1_core/os_coos.md"),
        tier=1,
        component="os_coos",
        content="",
        content_hash="deadbeef",
    )
    sec = ParsedSection(
        section_id="sec:os_coos.md#概要",
        file_path=doc.file_path,
        heading="概要",
        level=2,
        line_start=1,
        line_end=5,
        body_text="This section only summarizes ownership; it never discusses rendezvous semantics.",
        keywords=["CSPCommunication"],
    )

    import json
    verdict = json.loads(assessor._call_heuristic(doc, sec))
    assert verdict["recommended_verification"] != "pyModelChecking", \
        "citing {CSPCommunication} as a tag must not alone trigger formal verification"


def test_waived_section_is_not_flagged_as_an_llm_candidate_either():
    """A section explicitly waived from verification must be exempt from
    BOTH formal_triggers and llm_triggers -- the waiver check originally
    only guarded the formal branch, so a waived section carrying an
    llm_trigger word (e.g. "rationale") was still recommended for
    LLM_Judge despite the waiver saying it needs no verification at all."""
    cfg = Config()
    cfg.risk_assessment = RiskAssessmentConfig(
        heuristic=HeuristicConfig(
            formal_triggers=[],
            llm_triggers=["rationale"],
            waivers=[WaiverRule(
                section_pattern=r"components/tier1_core/os_coos\.md",
                heading_pattern=r"^概要$",
                rationale="declarative table, no design tradeoff to audit",
                authorized_at="2026-08-24",
            )],
        )
    )
    assessor = RiskAssessor(cfg)

    doc = ParsedDocument(
        file_path="components/tier1_core/os_coos.md",
        full_path=Path("components/tier1_core/os_coos.md"),
        tier=1,
        component="os_coos",
        content="",
        content_hash="deadbeef",
    )
    sec = ParsedSection(
        section_id="sec:os_coos.md#概要",
        file_path=doc.file_path,
        heading="概要",
        level=2,
        line_start=1,
        line_end=5,
        body_text="The rationale for this table is purely declarative.",
        keywords=[],
    )

    import json
    verdict = json.loads(assessor._call_heuristic(doc, sec))
    assert verdict["recommended_verification"] == "Static", \
        "a waived section must not be recommended for LLM_Judge just because it contains a trigger word"
