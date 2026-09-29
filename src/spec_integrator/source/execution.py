"""External tool and test execution for source verification."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.source.models import SourceIssue


def _pysim_failure_details(stdout: str, stderr: str) -> str:
    lines = stdout.splitlines()
    failure_sections: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].lstrip().startswith("[FAIL]"):
            index += 1
            continue

        end = index + 1
        while end < len(lines):
            line = lines[end].lstrip()
            if line.startswith(("[PASS]", "[FAIL]")) or "Unit Test Summary:" in line:
                break
            end += 1
        failure_sections.append("\n".join(lines[index:end]).strip())
        index = end

    summary = next(
        (line.strip() for line in reversed(lines) if "Unit Test Summary:" in line),
        "",
    )
    if failure_sections:
        details = "\n".join((*failure_sections, summary) if summary else failure_sections)
    else:
        details = stderr.strip() or stdout.strip()

    if not details:
        return "Exit code non-zero"
    if len(details) > 4000:
        details = f"{details[:1900]}\n... failure output truncated ...\n{details[-1900:]}"
    return details


class SourceExecution:
    """Runs external source tools and translates failures into source issues."""

    def __init__(self, config: Config):
        self.root_dir = config.config_dir

    def _relative_path(self, file_path: Path) -> str:
        if file_path.is_relative_to(self.root_dir):
            return str(file_path.relative_to(self.root_dir)).replace("\\", "/")
        return str(file_path)

    def run_ruff(self, files: list[Path], group_name: str) -> list[SourceIssue]:
        ruff_bin = shutil.which("ruff")
        command = (
            [ruff_bin] if ruff_bin else ["uv", "run", "--system-certs", "--with", "ruff", "ruff"]
        )
        result = subprocess.run(
            [*command, "check", *[str(file_path) for file_path in files]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return []
        issues: list[SourceIssue] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith("Found "):
                issues.append(
                    SourceIssue(
                        file_path="[python]",
                        line=1,
                        rule="PY-RUFF-LINT",
                        severity="ERROR",
                        message=line,
                        group=group_name,
                    )
                )
        return issues

    def execute_python_file(self, file_path: Path, group_name: str) -> list[SourceIssue]:
        environment = os.environ.copy()
        root_path = str(self.root_dir)
        existing_python_path = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            f"{root_path}{os.pathsep}{existing_python_path}" if existing_python_path else root_path
        )
        command = [
            "uv",
            "run",
            "--system-certs",
            "--project",
            "tools/spec-integrator",
            "python",
            str(file_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=environment,
            )
            if result.returncode == 0:
                return []
            error = result.stderr.strip() or result.stdout.strip()
            last_line = error.splitlines()[-1] if error else "Exit code non-zero"
            return [
                SourceIssue(
                    file_path=self._relative_path(file_path),
                    line=1,
                    rule="PY-EXECUTION-FAILED",
                    severity="ERROR",
                    message=f"Execution failed: {last_line}",
                    group=group_name,
                )
            ]
        except subprocess.TimeoutExpired:
            return [
                SourceIssue(
                    file_path=self._relative_path(file_path),
                    line=1,
                    rule="PY-EXECUTION-TIMEOUT",
                    severity="ERROR",
                    message="Execution timed out after 30 seconds.",
                    group=group_name,
                )
            ]
        except Exception as error:
            return [
                SourceIssue(
                    file_path=self._relative_path(file_path),
                    line=1,
                    rule="PY-EXECUTION-ERROR",
                    severity="ERROR",
                    message=f"Failed to execute: {error}",
                    group=group_name,
                )
            ]

    def run_pysim_tests(self, group_name: str) -> list[SourceIssue]:
        runner = self.root_dir / "experiments/pysim/qa/run_all.py"
        if not runner.exists():
            return []
        project_python = self.root_dir / ".venv" / "Scripts" / "python.exe"
        if not project_python.exists():
            project_python = self.root_dir / ".venv" / "bin" / "python"
        test_python = str(project_python) if project_python.exists() else sys.executable
        try:
            result = subprocess.run(
                [test_python, str(runner)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode == 0:
                return []
            failure_details = _pysim_failure_details(result.stdout, result.stderr)
            return [
                SourceIssue(
                    file_path=self._relative_path(runner),
                    line=1,
                    rule="PYSIM-TEST-FAILED",
                    severity="ERROR",
                    message=f"Pysim test suite failed:\n{failure_details}",
                    group=group_name,
                )
            ]
        except Exception as error:
            return [
                SourceIssue(
                    file_path=self._relative_path(runner),
                    line=1,
                    rule="PYSIM-TEST-ERROR",
                    severity="ERROR",
                    message=f"Failed to run pysim tests: {error}",
                    group=group_name,
                )
            ]


__all__ = ["SourceExecution"]
