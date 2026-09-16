"""Public document facade."""

from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.document.pipeline import DocumentCheckResult, DocumentPipeline
from spec_integrator.document.workspace import DocumentWorkspace


class DocumentFacade:
    """Stable entry point for document-oriented commands."""

    def __init__(self, config: Config):
        self.pipeline = DocumentPipeline(config)

    def load_workspace(
        self,
        clean: bool = False,
        file_paths: list[str | Path] | None = None,
    ) -> DocumentWorkspace:
        return self.pipeline.load_workspace(clean=clean, file_paths=file_paths)

    def check(self, workspace: DocumentWorkspace, report_path: Path) -> DocumentCheckResult:
        return self.pipeline.check(workspace, report_path)

    def build_terms(self, workspace: DocumentWorkspace) -> int:
        return self.pipeline.build_terms(workspace)

    def format(self, file_paths: list[str | Path] | None = None) -> tuple[int, int]:
        return self.pipeline.format(file_paths=file_paths)


__all__ = ["DocumentCheckResult", "DocumentFacade"]
