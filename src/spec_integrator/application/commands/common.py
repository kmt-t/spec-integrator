"""Shared workspace loading for application commands."""

from __future__ import annotations

from pathlib import Path

from spec_integrator.application.utils import _log, _rel_path
from spec_integrator.config import Config
from spec_integrator.document import DocumentService


def _load_and_parse_all(
    config: Config, clean: bool = False, file_paths: list[str | Path] | None = None
):
    document = DocumentService(config)
    workspace = document.load_workspace(
        clean=clean,
        file_paths=file_paths,
    )
    _log(f"Parsed {len(workspace.documents)} document(s) in {_rel_path(workspace.docs_root)}.")
    _log(f"DocGraph built: {len(workspace.graph.nodes)} nodes, {len(workspace.graph.edges)} edges.")
    return workspace.documents, workspace.graph, workspace.db, workspace.docs_root
