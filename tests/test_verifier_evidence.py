import pytest
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.evidence import EvidenceVerifier
from spec_integrator.verifier.formal import FormalModelResult


def _parse(tmp_path, rel_name, body):
    docs_dir = tmp_path / "docs"
    target = docs_dir / rel_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    cfg = Config()
    cfg.config_dir = tmp_path
    doc = MarkdownParser(cfg).parse_file(target, docs_dir)
    return cfg, doc, docs_dir


def test_dangling_artifact_reference_is_rejected(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "components/tier1_interface/ipc_router.md", """# IPC Router
## Verification
The deadlock freedom is formalised in `formal/ipc_deadlock.py`.
""")
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir, [])
    codes = [i.rule_code for i in issues]
    assert "EVID-DANGLING-ARTIFACT-REF" in codes
    assert any("ipc_deadlock.py" in i.message for i in issues)


def test_existing_artifact_reference_is_accepted(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "components/tier1_core/os_coos.md", """# COOS
## Notes
See `os_scheduler.md` for details.
""")
    (docs_dir / "components" / "tier1_core" / "os_scheduler.md").write_text("# S\n", encoding="utf-8")

    issues = EvidenceVerifier(cfg).verify([doc], docs_dir, [])
    assert [i for i in issues if i.rule_code == "EVID-DANGLING-ARTIFACT-REF"] == []


def test_verification_claim_without_any_tag_is_rejected(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "plans/roadmap_phase.md", """# Roadmap
## Status
- [x] 形式検証（pyModelChecking 5モデル）の数学的証明完了
""")
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir, [])
    assert any(i.rule_code == "EVID-UNBACKED-CLAIM" for i in issues)


def test_verification_claim_is_accepted_when_a_model_passed(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "components/tier1_core/os_coos.md",
                                """# COOS {VERIFY_FORMAL}
## Verification
デッドロック不在は検証済みである。
""")
    passing = [FormalModelResult(component="tier1_core",
                                 model_file="components/tier1_core/formal/coos_model.py",
                                 status="PASS")]
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir, passing)
    assert [i for i in issues if i.rule_code == "EVID-UNBACKED-CLAIM"] == []


def test_verification_claim_rejected_when_the_model_did_not_pass(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "components/tier1_core/os_coos.md",
                                """# COOS {VERIFY_FORMAL}
## Verification
デッドロック不在は検証済みである。
""")
    vacuous = [FormalModelResult(component="tier1_core",
                                 model_file="components/tier1_core/formal/coos_model.py",
                                 status="VACUOUS")]
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir, vacuous)
    assert any(i.rule_code == "EVID-UNBACKED-CLAIM" for i in issues)


def test_asserted_measurement_without_artifact_is_rejected(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "architecture/concept_harness.md", """# Harness
## Performance
測定環境: Cortex-M7 @216MHz, GCC 14 `-O3`
""")
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir, [])
    assert any(i.rule_code == "EVID-UNSOURCED-MEASUREMENT" for i in issues)


def test_unsourced_metric_is_flagged_but_targets_are_not(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "components/tier2_jit/jit_compiler.md", """# JIT
## Cache
これにより JIT ヒット率 96.9% を維持する。
コンテキストスイッチの目標は 10 cycles 以内とする。
""")
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir, [])
    metric = [i for i in issues if i.rule_code == "EVID-UNSOURCED-METRIC"]
    assert len(metric) == 1
    assert metric[0].severity == "WARNING"


def test_code_blocks_are_not_scanned(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "components/tier1_core/system_config.md", """# Config
## Example
```python
# see nonexistent_helper.py for the reference implementation
hit_rate = 99.9
```
""")
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir, [])
    assert issues == []


def test_gate_can_be_disabled(tmp_path):
    cfg, doc, docs_dir = _parse(tmp_path, "plans/roadmap_phase.md", "# R\n## S\n証明完了。\n")
    cfg.evidence.enabled = False
    assert EvidenceVerifier(cfg).verify([doc], docs_dir, []) == []
