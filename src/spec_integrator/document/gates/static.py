from __future__ import annotations

from pathlib import Path

from spec_integrator.checks.format.broken_anchor import BrokenAnchorCheck
from spec_integrator.checks.format.broken_link import BrokenLinkCheck
from spec_integrator.checks.format.file_link import FileLinkFormatCheck
from spec_integrator.checks.format.invalid_mermaid import MermaidSyntaxCheck
from spec_integrator.checks.format.typo import LevenshteinTypoCheck
from spec_integrator.checks.hierarchy.hierarchy import HierarchyCheck
from spec_integrator.checks.readability.prose_readability import ProseReadabilityCheck
from spec_integrator.checks.traceability.traceability import TraceabilityCheck
from spec_integrator.config import Config
from spec_integrator.gates.base import GateContext
from spec_integrator.gates.runner import GateRunner
from spec_integrator.graph import Graph
from spec_integrator.models import ParsedDocument, VerificationIssue

__all__ = ["StaticGate"]


class StaticGate:
    """Format, Traceability, Hierarchy Gate: プラグイン化された Automated Gate チェック群を実行する。"""

    def __init__(self, config: Config):
        self.config = config
        self.runner = GateRunner(
            checks=[
                BrokenLinkCheck(),
                BrokenAnchorCheck(),
                FileLinkFormatCheck(),
                MermaidSyntaxCheck(),
                LevenshteinTypoCheck(),
                TraceabilityCheck(),
                HierarchyCheck(),
                ProseReadabilityCheck(),
            ]
        )

    def verify(
        self, documents: list[ParsedDocument], graph: Graph, docs_root: Path
    ) -> list[VerificationIssue]:
        ctx = GateContext(
            documents=documents,
            graph=graph,
            docs_root=docs_root,
            config=self.config,
        )
        return self.runner.run(ctx)
