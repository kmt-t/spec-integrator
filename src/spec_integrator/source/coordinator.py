"""Source verification coordination."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from spec_integrator.source.analyzer import SourceAnalyzer
from spec_integrator.source.models import SourceVerificationResult

if TYPE_CHECKING:
    from spec_integrator.config import Config


class SourceVerifier:
    """Coordinates source discovery and analysis for one verification request."""

    def __init__(self, config: Config):
        self.analyzer = SourceAnalyzer(config)

    def resolve_group_names(self, group_filter: str | None = None) -> list[str]:
        return self.analyzer.resolve_group_names(group_filter)

    def collect_files_for_group(
        self,
        group_name: str,
        explicit_files: list[str | Path] | None = None,
    ) -> list[Path]:
        return self.analyzer.collect_files_for_group(group_name, explicit_files)

    def verify_group(
        self,
        group_name: str,
        files: list[Path],
    ) -> SourceVerificationResult:
        return self.analyzer.verify_group(group_name, files)


__all__ = ["SourceVerifier"]
