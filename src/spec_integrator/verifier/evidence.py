from __future__ import annotations

from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageContext
from spec_integrator.anti_sabotage.checks.evid_benchmark_missing import (
    BenchmarkScriptMissingCheck,
)
from spec_integrator.anti_sabotage.checks.evid_dangling_artifact_ref import (
    DanglingArtifactRefCheck,
)
from spec_integrator.anti_sabotage.checks.evid_declared_file_missing import (
    DeclaredEvidenceFileMissingCheck,
)
from spec_integrator.anti_sabotage.checks.evid_tag_undeclared import (
    TagToEvidenceMismatchCheck,
)
from spec_integrator.anti_sabotage.runner import AntiSabotageRunner
from spec_integrator.config import Config
from spec_integrator.models import FormalModelResult, ParsedDocument, VerificationIssue

__all__ = ["EvidenceVerifier"]


class EvidenceVerifier:
    """Evidence Gate: プラグイン化された Anti-Sabotage Evidence チェック群を実行する。"""

    def __init__(self, config: Config):
        self.config = config
        self.runner = AntiSabotageRunner(
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
        ctx = AntiSabotageContext(
            documents=documents,
            graph=None,
            docs_root=docs_root,
            config=self.config,
            formal_results=formal_results,
            wit_results=wit_results,
        )
        return self.runner.run(ctx)
