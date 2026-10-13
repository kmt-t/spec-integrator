from __future__ import annotations

from pathlib import Path

from spec_integrator.checks.evidence.benchmark_missing import (
    BenchmarkScriptMissingCheck,
)
from spec_integrator.checks.evidence.dangling_artifact_ref import (
    DanglingArtifactRefCheck,
)
from spec_integrator.checks.evidence.declared_file_missing import (
    DeclaredEvidenceFileMissingCheck,
)
from spec_integrator.checks.evidence.tag_undeclared import (
    TagToEvidenceMismatchCheck,
)
from spec_integrator.config import Config
from spec_integrator.gates.base import GateContext
from spec_integrator.gates.runner import GateRunner
from spec_integrator.models import FormalModelResult, ParsedDocument, VerificationIssue

__all__ = ["EvidenceGate"]


class EvidenceGate:
    """Evidence Gate: プラグイン化された Automated Gate Evidence チェック群を実行する。"""

    def __init__(self, config: Config):
        self.config = config
        self.runner = GateRunner(
            checks=[
                DeclaredEvidenceFileMissingCheck(),
                TagToEvidenceMismatchCheck(),
                BenchmarkScriptMissingCheck(),
                DanglingArtifactRefCheck(),
            ]
        )

    def verify(
        self,
        documents: list[ParsedDocument],
        docs_root: Path,
        formal_results: list[FormalModelResult] | None = None,
        wit_results: list | None = None,
    ) -> list[VerificationIssue]:
        if not self.config.evidence.enabled:
            return []
        ctx = GateContext(
            documents=documents,
            graph=None,
            docs_root=docs_root,
            config=self.config,
            formal_results=formal_results,
            wit_results=wit_results,
        )
        return self.runner.run(ctx)
