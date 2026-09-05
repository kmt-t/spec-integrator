from __future__ import annotations

from pathlib import Path
from typing import Any

from spec_integrator.anti_sabotage.base import AntiSabotageContext
from spec_integrator.anti_sabotage.checks.consist_duplicate_definition import (
    DuplicateDefinitionCheck,
)
from spec_integrator.anti_sabotage.checks.consist_stale_value import StaleValueCheck
from spec_integrator.anti_sabotage.checks.consist_symbol_drift import (
    SymbolDriftCheck,
)
from spec_integrator.anti_sabotage.runner import AntiSabotageRunner
from spec_integrator.config import Config
from spec_integrator.models import (
    ConsistencySummary,
    ParsedDocument,
    SymbolDrift,
    VerificationIssue,
)

__all__ = ["ConsistencySummary", "ConsistencyVerifier", "SymbolDrift"]


class ConsistencyVerifier:
    """Consistency Gate: プラグイン化された Anti-Sabotage 一貫性チェック群を実行する。"""

    def __init__(self, config: Config):
        self.config = config
        self.runner = AntiSabotageRunner(
            checks=[
                DuplicateDefinitionCheck(),
                SymbolDriftCheck(),
                StaleValueCheck(),
            ]
        )

    def verify(
        self,
        documents: list[ParsedDocument],
        docs_root: Path,
        db: Any | None = None,
    ) -> tuple[list[VerificationIssue], ConsistencySummary]:
        summary = ConsistencySummary()
        if not self.config.consistency.enabled:
            return [], summary

        ctx = AntiSabotageContext(
            documents=documents,
            graph=None,
            docs_root=docs_root,
            config=self.config,
            db=db,
            extra={"summary": summary},
        )
        issues = self.runner.run(ctx)
        return issues, summary
