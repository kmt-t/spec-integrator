import pytest
from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.parser import MarkdownParser
from spec_integrator.verifier.formal import FormalVerifier


_HEADER = '''
from pyModelChecking import Kripke
from pyModelChecking.CTL import AG, EF, AF, Not, And, AtomicProposition
'''

# A model in which the violating state (both processes critical) IS representable
# and IS reachable. The property is therefore genuinely falsifiable.
FALSIFIABLE_MODEL = _HEADER + '''
BACKS = ["tier1_core/scheduler.md"]


def build_model():
    S = ["s_idle", "s_a_crit", "s_b_crit", "s_both_crit", "s_wait"]
    R = [
        ("s_idle", "s_a_crit"), ("s_idle", "s_b_crit"), ("s_idle", "s_wait"),
        ("s_a_crit", "s_both_crit"), ("s_a_crit", "s_idle"),
        ("s_b_crit", "s_idle"), ("s_b_crit", "s_wait"),
        ("s_both_crit", "s_idle"),
        ("s_wait", "s_a_crit"), ("s_wait", "s_idle"),
    ]
    L = {
        "s_idle": {"idle"},
        "s_a_crit": {"a_crit"},
        "s_b_crit": {"b_crit"},
        "s_both_crit": {"a_crit", "b_crit"},
        "s_wait": {"waiting"},
    }
    return Kripke(S=S, S0={"s_idle"}, R=R, L=L)


def properties():
    bad = And(AtomicProposition("a_crit"), AtomicProposition("b_crit"))
    return [{
        "name": "mutual_exclusion",
        "kind": "safety",
        "logic": "CTL",
        "formula": AG(Not(bad)),
        "violation": bad,
        # This naive model does NOT enforce mutual exclusion, and says so honestly.
        "expect": False,
    }]
'''

# Same claim, but the violating state was simply never enumerated: the property
# holds by construction of the state space rather than by design.
VACUOUS_MODEL = _HEADER + '''
BACKS = ["tier1_core/scheduler.md"]


def build_model():
    S = ["s_idle", "s_a_crit", "s_b_crit", "s_wait"]
    R = [
        ("s_idle", "s_a_crit"), ("s_idle", "s_b_crit"), ("s_idle", "s_wait"),
        ("s_a_crit", "s_idle"), ("s_b_crit", "s_idle"),
        ("s_wait", "s_a_crit"), ("s_wait", "s_idle"),
    ]
    L = {
        "s_idle": {"idle"},
        "s_a_crit": {"a_crit"},
        "s_b_crit": {"b_crit"},
        "s_wait": {"waiting"},
    }
    return Kripke(S=S, S0={"s_idle"}, R=R, L=L)


def properties():
    bad = And(AtomicProposition("a_crit"), AtomicProposition("b_crit"))
    return [{
        "name": "mutual_exclusion",
        "kind": "safety",
        "logic": "CTL",
        "formula": AG(Not(bad)),
        "violation": bad,
        "expect": True,
    }]
'''


def _make_doc(tmp_path, model_source, doc_name="scheduler.md"):
    docs_dir = tmp_path / "docs"
    comp_dir = docs_dir / "tier1_core"
    comp_dir.mkdir(parents=True)

    doc_file = comp_dir / doc_name
    doc_file.write_text(
        "# Scheduler Specification {VERIFY_FORMAL}\n## Details\nScheduler spec text.\n",
        encoding="utf-8")

    formal_dir = comp_dir / "formal"
    formal_dir.mkdir()
    (formal_dir / "sched_model.py").write_text(model_source, encoding="utf-8")

    cfg = Config()
    doc = MarkdownParser(cfg).parse_file(doc_file, docs_dir)
    return cfg, doc, docs_dir


def test_model_without_contract_is_rejected(tmp_path):
    """A model that only prints PASS is not auditable and must not pass the gate."""
    legacy = _HEADER + '''
def verify():
    km = Kripke(S=["s0", "s1"], S0={"s0"}, R=[("s0", "s1"), ("s1", "s0")],
                L={"s0": {"idle"}, "s1": {"busy"}})
    print("Model check PASS")
    return 0
'''
    cfg, doc, docs_dir = _make_doc(tmp_path, legacy)
    issues, results = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert len(results) == 1
    assert results[0].status == "NO_CONTRACT"
    assert any(i.rule_code == "FORMAL-MODEL-NO-CONTRACT" for i in issues)


def test_falsifiable_model_passes(tmp_path):
    """The violation is representable and reachable, so the verdict is meaningful."""
    cfg, doc, docs_dir = _make_doc(tmp_path, FALSIFIABLE_MODEL)
    issues, results = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert results[0].status == "PASS", results[0].details
    assert results[0].properties[0].status == "PASS"
    assert [i for i in issues if i.gate == "Formal"] == []


def test_vacuous_safety_property_is_rejected(tmp_path):
    """AG(not bad) is not a proof when no state can ever satisfy `bad`."""
    cfg, doc, docs_dir = _make_doc(tmp_path, VACUOUS_MODEL)
    issues, results = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert results[0].status == "VACUOUS"
    assert results[0].properties[0].status == "VACUOUS"
    assert any(i.rule_code == "FORMAL-PROPERTY-VACUOUS" for i in issues)


def test_single_path_model_is_rejected(tmp_path):
    """A deterministic cycle cannot exhibit deadlock, so claims over it are meaningless."""
    single_path = _HEADER + '''
def build_model():
    S = ["s0", "s1", "s2", "s3"]
    R = [("s0", "s1"), ("s1", "s2"), ("s2", "s3"), ("s3", "s0")]
    L = {"s0": {"sender_owns"}, "s1": {"in_flight"},
         "s2": {"receiver_owns"}, "s3": {"idle"}}
    return Kripke(S=S, S0={"s0"}, R=R, L=L)

def properties():
    return [{
        "name": "transfer_completes", "kind": "liveness", "logic": "CTL",
        "formula": AF(AtomicProposition("receiver_owns")),
        "violation": AtomicProposition("in_flight"),
        "expect": True,
    }]
'''
    cfg, doc, docs_dir = _make_doc(tmp_path, single_path)
    issues, results = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert any(i.rule_code == "FORMAL-MODEL-UNSOUND" for i in issues)
    assert any("deterministic path" in i.message for i in issues)


def test_unreachable_state_is_rejected(tmp_path):
    """A state drawn in the diagram but not wired into R is a modelling error."""
    orphaned = FALSIFIABLE_MODEL.replace('("s_a_crit", "s_both_crit"), ', "")
    cfg, doc, docs_dir = _make_doc(tmp_path, orphaned)
    issues, results = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert any(i.rule_code == "FORMAL-MODEL-UNSOUND" for i in issues)
    assert any("unreachable" in i.message for i in issues)


def test_liveness_declared_without_eventuality_operator_is_rejected(tmp_path):
    mislabelled = FALSIFIABLE_MODEL.replace('"kind": "safety"', '"kind": "liveness"')
    cfg, doc, docs_dir = _make_doc(tmp_path, mislabelled)
    issues, _ = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert any(i.rule_code == "FORMAL-PROPERTY-INVALID" for i in issues)
    assert any("eventuality operator" in i.message for i in issues)


def test_property_over_undefined_proposition_is_rejected(tmp_path):
    typo = FALSIFIABLE_MODEL.replace('AtomicProposition("b_crit")',
                                     'AtomicProposition("b_critical")')
    cfg, doc, docs_dir = _make_doc(tmp_path, typo)
    issues, _ = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert any(i.rule_code == "FORMAL-PROPERTY-INVALID" for i in issues)
    assert any("never appear in any state label" in i.message for i in issues)


def test_shared_model_cannot_back_two_documents_silently(tmp_path):
    """One model must not be counted as proof for several unrelated specifications."""
    cfg, doc, docs_dir = _make_doc(tmp_path, FALSIFIABLE_MODEL)

    second = docs_dir / "tier1_core" / "coos.md"
    second.write_text("# COOS {VERIFY_FORMAL}\n## Details\nText.\n", encoding="utf-8")
    doc2 = MarkdownParser(cfg).parse_file(second, docs_dir)

    issues, results = FormalVerifier(cfg).verify_documents([doc, doc2], docs_dir)

    # The model script is executed once, not once per claimant.
    assert len(results) == 1
    assert len({r.model_file for r in results}) == 1
    assert sorted(results[0].backing_documents) == ["tier1_core/coos.md",
                                                    "tier1_core/scheduler.md"]
    ambiguous = [i for i in issues if i.rule_code == "FORMAL-BACKING-AMBIGUOUS"]
    # scheduler.md is declared in BACKS; coos.md is not.
    assert len(ambiguous) == 1
    assert ambiguous[0].file_path == "tier1_core/coos.md"


def test_missing_model_for_tagged_document_is_rejected(tmp_path):
    docs_dir = tmp_path / "docs"
    comp_dir = docs_dir / "tier1_core"
    comp_dir.mkdir(parents=True)
    doc_file = comp_dir / "sched.md"
    doc_file.write_text("# Sched {VERIFY_FORMAL}\n## D\nText.\n", encoding="utf-8")

    cfg = Config()
    doc = MarkdownParser(cfg).parse_file(doc_file, docs_dir)
    issues, results = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert results[0].status == "NOT_FOUND"
    assert any(i.rule_code == "FORMAL-MODEL-NOT-FOUND" for i in issues)


def test_liveness_claimed_with_existential_eventuality_is_rejected(tmp_path):
    """AG(p -> EF q) proves q is reachable, not that it inevitably happens."""
    weak = _HEADER + '''
def build_model():
    S = ["s_idle", "s_req", "s_done", "s_spin"]
    R = [("s_idle", "s_req"), ("s_req", "s_done"), ("s_req", "s_spin"),
         ("s_spin", "s_req"), ("s_done", "s_idle")]
    L = {"s_idle": {"idle"}, "s_req": {"requested"},
         "s_done": {"progress"}, "s_spin": {"spinning"}}
    return Kripke(S=S, S0={"s_idle"}, R=R, L=L)

def properties():
    from pyModelChecking.CTL import Imply
    return [{
        "name": "request_progresses", "kind": "liveness", "logic": "CTL",
        "formula": AG(Imply(AtomicProposition("requested"),
                            EF(AtomicProposition("progress")))),
        "expect": True,
    }]
'''
    cfg, doc, docs_dir = _make_doc(tmp_path, weak)
    issues, _ = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert any(i.rule_code == "FORMAL-PROPERTY-INVALID" for i in issues)
    assert any("existential eventuality" in i.message for i in issues)


def test_liveness_with_universal_eventuality_is_accepted(tmp_path):
    strong = _HEADER + '''
def build_model():
    S = ["s_idle", "s_req", "s_done", "s_alt"]
    R = [("s_idle", "s_req"), ("s_idle", "s_alt"), ("s_alt", "s_idle"),
         ("s_req", "s_done"), ("s_done", "s_idle")]
    L = {"s_idle": {"idle"}, "s_req": {"requested"},
         "s_done": {"progress"}, "s_alt": {"other"}}
    return Kripke(S=S, S0={"s_idle"}, R=R, L=L)

def properties():
    from pyModelChecking.CTL import Imply
    return [{
        "name": "request_progresses", "kind": "liveness", "logic": "CTL",
        "formula": AG(Imply(AtomicProposition("requested"),
                            AF(AtomicProposition("progress")))),
        "expect": True,
    }]
'''
    cfg, doc, docs_dir = _make_doc(tmp_path, strong)
    issues, results = FormalVerifier(cfg).verify_documents([doc], docs_dir)

    assert results[0].status == "PASS", results[0].details
    assert [i for i in issues if i.gate == "Formal"] == []
