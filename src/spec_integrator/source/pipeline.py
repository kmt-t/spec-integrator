"""Source-side application pipeline."""

import shutil
import subprocess
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.source.coordinator import SourceVerifier
from spec_integrator.source.models import SourceVerificationResult


class SourcePipeline:
    """Coordinates source verification and source formatting operations."""

    def __init__(self, config: Config):
        self.config = config
        self.verifier = SourceVerifier(config)

    def check(
        self,
        group: str | None = None,
        file_paths: list[str | Path] | None = None,
    ) -> list[SourceVerificationResult]:
        results: list[SourceVerificationResult] = []
        for group_name in self.verifier.resolve_group_names(group):
            files = self.verifier.collect_files_for_group(group_name, file_paths)
            results.append(self.verifier.verify_group(group_name, files))
        return results

    def format(
        self,
        group: str | None = None,
        file_paths: list[str | Path] | None = None,
    ) -> int:
        formatted = 0
        for group_name in self.verifier.resolve_group_names(group):
            files = self.verifier.collect_files_for_group(group_name, file_paths)
            group_config = self.config.source_verification.groups.get(group_name)
            formatters = group_config.formatters if group_config else ["ruff"]
            for formatter in formatters:
                if formatter == "ruff":
                    python_files = [str(path) for path in files if path.suffix.lower() == ".py"]
                    if not python_files:
                        continue
                    ruff_bin = shutil.which("ruff")
                    command = [ruff_bin] if ruff_bin else ["uv", "run", "--system-certs", "--with", "ruff", "ruff"]
                    subprocess.run([*command, "check", "--fix", *python_files], check=False)
                    subprocess.run([*command, "format", *python_files], check=False)
                    formatted += len(python_files)
                elif formatter == "clang-format":
                    cpp_files = [
                        str(path)
                        for path in files
                        if path.suffix.lower() in (".hxx", ".cxx", ".c", ".h", ".cpp")
                    ]
                    clang_format = shutil.which("clang-format")
                    if cpp_files and clang_format:
                        subprocess.run([clang_format, "-i", *cpp_files], check=False)
                        formatted += len(cpp_files)
        return formatted


__all__ = ["SourcePipeline"]
