import pytest
from pathlib import Path
from spec_integrator.config import Config, TierConfig, KeywordRule
from spec_integrator.parser import MarkdownParser
from spec_integrator.graph import DocGraphBuilder
from spec_integrator.verifier.static import StaticVerifier


def test_static_verifier_gates(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    # Setup configuration with 3 Tiers
    cfg = Config()
    cfg.tiers = [
        TierConfig(tier=0, name="Reqs", path_pattern="requires/**/*.md"),
        TierConfig(tier=1, name="Core", path_pattern="tier1_*/**/*.md"),
        TierConfig(tier=2, name="Runtime", path_pattern="tier2_*/**/*.md"),
    ]
    cfg.keywords["local"] = KeywordRule(pattern="^REQ_[A-Z0-9_]+$", defined_in="requires/**/*.md")

    # 1. Tier 0
    (docs_dir / "requires").mkdir()
    (docs_dir / "requires" / "req.md").write_text("""# System Reqs
## Sched {REQ_COOS_SCHED}
## Unreferenced {REQ_UNREF}
""", encoding="utf-8")

    # 2. Tier 1 (Valid reference, but has broken link and undefined keyword)
    (docs_dir / "tier1_core").mkdir()
    (docs_dir / "tier1_core" / "sched.md").write_text("""# Core Scheduler
## Implementation
Implements {REQ_COOS_SCHED} and undefined {REQ_NON_EXIST}.
Broken link to [Missing](non_existing.md).
Broken anchor to [Req](../requires/req.md#missing-anchor).
Valid link to [Req](../requires/req.md#sched).
""", encoding="utf-8")

    # 3. Tier 2 (Reverse dependency test: links or refers to lower/upper tier)
    # Tier 0 linking directly to Tier 2 (Reverse dependency)
    (docs_dir / "tier2_runtime").mkdir()
    (docs_dir / "tier2_runtime" / "loader.md").write_text("""# Loader
## Details
Runtime details.
""", encoding="utf-8")

    # Modify Tier 0 to contain a reverse link to Tier 2
    (docs_dir / "requires" / "req.md").write_text("""# System Reqs
## Sched {REQ_COOS_SCHED}
## Unreferenced {REQ_UNREF}
Direct link to [Loader](../tier2_runtime/loader.md).
""", encoding="utf-8")

    # Parse and build graph
    parser = MarkdownParser(cfg)
    files = list(docs_dir.rglob("*.md"))
    docs = [parser.parse_file(f, docs_dir) for f in files]
    builder = DocGraphBuilder(cfg)
    graph = builder.build(docs, docs_dir)

    verifier = StaticVerifier(cfg)
    issues = verifier.verify(docs, graph, docs_dir)

    rule_codes = [i.rule_code for i in issues]

    # Check that all gate violations are caught
    assert "FMT-BROKEN-LINK" in rule_codes
    assert "FMT-BROKEN-ANCHOR" in rule_codes
    assert "TRACE-UNDEFINED-KEYWORD" in rule_codes
    assert "TRACE-UNREFERENCED-REQUIREMENT" in rule_codes
    assert "HIERARCHY-REVERSE-DEPENDENCY" in rule_codes
