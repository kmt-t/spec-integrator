import pytest
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.formal import FormalVerifier


def test_formal_verifier(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    comp_dir = docs_dir / "tier1_core"
    comp_dir.mkdir()

    # Doc with {VERIFY_FORMAL}
    doc_file = comp_dir / "scheduler.md"
    doc_file.write_text("""# Scheduler Specification {VERIFY_FORMAL}
## Details
Scheduler spec text.
""", encoding="utf-8")

    # Formal directory with a passing pyModelChecking script
    formal_dir = comp_dir / "formal"
    formal_dir.mkdir()
    model_script = formal_dir / "sched_model.py"
    model_script.write_text("""
from pyModelChecking import Kripke
from pyModelChecking.CTL import modelcheck, AG, AtomicProposition

def verify():
    S = ["s0", "s1"]
    S0 = {"s0"}
    R = [("s0", "s1"), ("s1", "s0")]
    L = {"s0": {"idle"}, "s1": {"busy"}}
    km = Kripke(S=S, S0=S0, R=R, L=L)
    
    phi = AG(AtomicProposition("idle"))
    # Not all states are idle, so this would be false for entire Kripke, but let's test a true property
    phi_true = AG(AtomicProposition("idle") | AtomicProposition("busy"))
    sat = modelcheck(km, phi_true)
    assert km.S0.issubset(sat)
    print("Model check PASS")

if __name__ == "__main__":
    verify()
""", encoding="utf-8")

    cfg = Config()
    parser = MarkdownParser(cfg)
    doc = parser.parse_file(doc_file, docs_dir)

    verifier = FormalVerifier(cfg)
    issues, results = verifier.verify_documents([doc], docs_dir)

    assert len(results) == 1
    assert results[0].status == "PASS"
    assert len(issues) == 0
