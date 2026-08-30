from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.evidence import EvidenceVerifier


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
    cfg, doc, docs_dir = _parse(
        tmp_path,
        "components/tier1_interface/ipc_router.md",
        """# IPC Router
## Verification
The deadlock freedom is formalised in `formal/ipc_deadlock.py`.
""",
    )
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir)
    codes = [i.rule_code for i in issues]
    assert "EVID-DANGLING-ARTIFACT-REF" in codes
    assert any("ipc_deadlock.py" in i.message for i in issues)


def test_existing_artifact_reference_is_accepted(tmp_path):
    cfg, doc, docs_dir = _parse(
        tmp_path,
        "components/tier1_core/os_coos.md",
        """# COOS
## Notes
See `os_scheduler.md` for details.
""",
    )
    (docs_dir / "components" / "tier1_core" / "os_scheduler.md").write_text(
        "# S\n", encoding="utf-8"
    )
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir)
    assert [i for i in issues if i.rule_code == "EVID-DANGLING-ARTIFACT-REF"] == []


def test_code_blocks_are_not_scanned_for_artifact_refs(tmp_path):
    cfg, doc, docs_dir = _parse(
        tmp_path,
        "components/tier1_core/system_config.md",
        """# Config
## Example
```python
# This is a sample code reference that does not exist on disk
import nonexistent_module.py
```
""",
    )
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir)
    assert issues == []


def test_ignored_artifact_refs_are_skipped(tmp_path):
    cfg, doc, docs_dir = _parse(
        tmp_path,
        "plans/roadmap.md",
        """
# Roadmap
See `reports/doc_report.md` for latest results.
""",
    )
    cfg.evidence.ignore_artifact_refs = ["reports/doc_report.md"]
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir)
    assert issues == []


def test_verify_benchmark_tag_without_script_is_rejected(tmp_path):
    cfg, doc, docs_dir = _parse(
        tmp_path,
        "components/tier3_jit/jit_compiler.md",
        """# JIT Compiler {VERIFY_BENCHMARK}
## Claim
Compilation is fast enough that optimization is unnecessary.
""",
    )
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir)
    codes = [i.rule_code for i in issues]
    assert "EVID-BENCHMARK-MISSING" in codes


def test_verify_benchmark_tag_with_script_is_accepted(tmp_path):
    cfg, doc, docs_dir = _parse(
        tmp_path,
        "components/tier3_jit/jit_compiler.md",
        """# JIT Compiler {VERIFY_BENCHMARK}
## Claim
Compilation is fast enough that optimization is unnecessary.
""",
    )
    bench_dir = docs_dir / "components" / "tier3_jit" / "benchmarks"
    bench_dir.mkdir(parents=True)
    (bench_dir / "compile_cost_bench.py").write_text("# real benchmark\n", encoding="utf-8")
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir)
    assert [i for i in issues if i.rule_code == "EVID-BENCHMARK-MISSING"] == []


def test_no_verify_benchmark_tag_is_not_checked(tmp_path):
    cfg, doc, docs_dir = _parse(
        tmp_path,
        "components/tier3_jit/jit_compiler.md",
        """# JIT Compiler
## Claim
Compilation is fast enough that optimization is unnecessary.
""",
    )
    issues = EvidenceVerifier(cfg).verify([doc], docs_dir)
    assert [i for i in issues if i.rule_code == "EVID-BENCHMARK-MISSING"] == []
