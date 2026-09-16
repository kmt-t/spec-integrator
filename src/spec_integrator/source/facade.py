"""Public source facade."""

from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.source.coordinator import SourceVerifier
from spec_integrator.source.models import SourceIssue, SourceVerificationResult
from spec_integrator.source.pipeline import SourcePipeline


class SourceFacade:
    """Stable entry point for source-oriented commands."""

    def __init__(self, config: Config):
        self.pipeline = SourcePipeline(config)

    def check(
        self,
        group: str | None = None,
        file_paths: list[str | Path] | None = None,
    ) -> list[SourceVerificationResult]:
        return self.pipeline.check(group=group, file_paths=file_paths)

    def format(
        self,
        group: str | None = None,
        file_paths: list[str | Path] | None = None,
    ) -> int:
        return self.pipeline.format(group=group, file_paths=file_paths)


__all__ = ["SourceFacade", "SourceIssue", "SourceVerificationResult", "SourceVerifier"]
