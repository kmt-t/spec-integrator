from __future__ import annotations

from pathlib import Path
from typing import Any

from spec_integrator.checks.consistency.duplicate_definition import (
    DuplicateDefinitionCheck,
)
from spec_integrator.checks.consistency.stale_value import StaleValueCheck
from spec_integrator.checks.consistency.symbol_drift import (
    SymbolDriftCheck,
)
from spec_integrator.config import Config
from spec_integrator.gates.base import GateContext
from spec_integrator.gates.runner import GateRunner
from spec_integrator.models import (
    ConsistencySummary,
    ParsedDocument,
    SymbolDrift,
    VerificationIssue,
)

__all__ = ["ConsistencyGate", "ConsistencySummary", "SymbolDrift"]


class ConsistencyGate:
    """Consistency Gate: プラグイン化された Automated Gate 一貫性チェック群を実行する。"""

    def __init__(self, config: Config):
        self.config = config
        self.runner = GateRunner(
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

        ctx = GateContext(
            documents=documents,
            graph=None,
            docs_root=docs_root,
            config=self.config,
            db=db,
            extra={"summary": summary},
        )
        issues = self.runner.run(ctx)
        return issues, summary
