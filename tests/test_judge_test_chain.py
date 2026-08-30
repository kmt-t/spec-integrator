from pathlib import Path
from spec_integrator.config import Config
from spec_integrator.judge.test_chain_judge import TestChainJudge, TestChainTarget


def test_test_chain_judge_target_discovery(tmp_path):
    # Setup dummy tree
    docs = tmp_path / "docs"
    tier = docs / "components" / "tier3_jit"
    tests = tier / "tests"
    tests.mkdir(parents=True)

    design_file = tier / "jit_compiler.md"
    design_file.write_text("# JIT Compiler\n\nCPS 4-argument convention.", encoding="utf-8")

    test_spec_file = tests / "jit_compiler_test_spec.md"
    test_spec_file.write_text("# JIT Compiler Test Spec\n\n| JITC-10 | CPS 4-arg |", encoding="utf-8")

    pysim = tmp_path / "experiments" / "pysim"
    pysim.mkdir(parents=True)
    test_code_file = pysim / "test_x64_jit.py"
    test_code_file.write_text("def test_cps_4arg(): pass", encoding="utf-8")

    cfg = Config()
    cfg.project.docs_root = str(docs)

    judge = TestChainJudge(cfg)
    targets = judge.auto_discover_targets(root_dir=docs)

    assert len(targets) == 1
    assert targets[0].component_name == "jit_compiler"
    assert targets[0].design_doc_path == design_file
    assert targets[0].test_spec_path == test_spec_file
    assert any("test_x64_jit.py" in str(p) for p in targets[0].test_code_paths)


def test_test_chain_judge_mock_execution(tmp_path):
    docs = tmp_path / "docs"
    tier = docs / "components" / "tier2_runtime"
    tests = tier / "tests"
    tests.mkdir(parents=True)

    design = tier / "runtime_interpreter.md"
    design.write_text("# Interpreter", encoding="utf-8")

    test_spec = tests / "runtime_interpreter_test_spec.md"
    test_spec.write_text("# Interpreter Test Spec", encoding="utf-8")

    cfg = Config()
    cfg.project.docs_root = str(docs)

    judge = TestChainJudge(cfg)
    targets = judge.auto_discover_targets(root_dir=docs)
    report = judge.judge_targets(targets, backend="mock")

    assert report.total_evaluated == 1
    assert report.pass_count == 1
    assert report.fail_count == 0
    assert report.results[0].status == "PASS"
