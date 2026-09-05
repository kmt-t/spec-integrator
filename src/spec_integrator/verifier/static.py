from __future__ import annotations

from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageContext
from spec_integrator.anti_sabotage.checks.fmt_broken_anchor import BrokenAnchorCheck
from spec_integrator.anti_sabotage.checks.fmt_broken_link import BrokenLinkCheck
from spec_integrator.anti_sabotage.checks.fmt_file_link import FileLinkFormatCheck
from spec_integrator.anti_sabotage.checks.fmt_hierarchy import HierarchyCheck
from spec_integrator.anti_sabotage.checks.fmt_invalid_mermaid import MermaidSyntaxCheck
from spec_integrator.anti_sabotage.checks.fmt_traceability import TraceabilityCheck
from spec_integrator.anti_sabotage.checks.fmt_typo import LevenshteinTypoCheck
from spec_integrator.anti_sabotage.runner import AntiSabotageRunner
from spec_integrator.config import Config
from spec_integrator.graph import Graph
from spec_integrator.models import ParsedDocument, VerificationIssue

__all__ = ["StaticVerifier"]


class StaticVerifier:
    """Format, Traceability, Hierarchy Gate: プラグイン化された Anti-Sabotage チェック群を実行する。"""

    def __init__(self, config: Config):
        self.config = config
        self.runner = AntiSabotageRunner(
            checks=[
                BrokenLinkCheck(),
                BrokenAnchorCheck(),
                FileLinkFormatCheck(),
                MermaidSyntaxCheck(),
                LevenshteinTypoCheck(),
                TraceabilityCheck(),
                HierarchyCheck(),
            ]
        )

    def verify(
        self, documents: list[ParsedDocument], graph: Graph, docs_root: Path
    ) -> list[VerificationIssue]:
        ctx = AntiSabotageContext(
            documents=documents,
            graph=graph,
            docs_root=docs_root,
            config=self.config,
        )
        return self.runner.run(ctx)
