from spec_integrator.source.execution import _pysim_failure_details


def test_pysim_failure_details_include_failed_test_output() -> None:
    stdout = "\n".join(
        (
            "  [FAIL] Scheduler (test_scheduler.py) 12.34 ms",
            "--- STDOUT ---",
            "E AssertionError: ready queue changed unexpectedly",
            "--- STDERR ---",
            "  [PASS] Containers (test_containers.py) 2.00 ms",
            " Unit Test Summary: 29/30 Passed, 1 Failed",
            "=" * 84,
        )
    )

    details = _pysim_failure_details(stdout, "")

    assert "[FAIL] Scheduler" in details
    assert "AssertionError: ready queue changed unexpectedly" in details
    assert "Unit Test Summary: 29/30 Passed, 1 Failed" in details
    assert "[PASS] Containers" not in details


def test_pysim_failure_details_fall_back_to_stderr() -> None:
    details = _pysim_failure_details("", "RuntimeError: runner failed")

    assert details == "RuntimeError: runner failed"
