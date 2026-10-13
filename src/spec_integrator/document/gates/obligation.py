from __future__ import annotations

from spec_integrator.checks.obligation.assessment_independence import (
    AssessmentIndependenceCheck,
)
from spec_integrator.checks.obligation.assessment_missing import (
    AssessmentMissingCheck,
)
from spec_integrator.checks.obligation.doc_judge_coverage import (
    DocumentJudgeCoverageCheck,
)
from spec_integrator.checks.obligation.judge_coverage import JudgeCoverageCheck
from spec_integrator.checks.obligation.verification_skipped import (
    VerificationTagSkippedCheck,
)
from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.gates.base import GateContext
from spec_integrator.gates.runner import GateRunner
from spec_integrator.graph import Graph
from spec_integrator.models import ObligationSummary, ParsedDocument, VerificationIssue

__all__ = ["ObligationGate", "ObligationSummary"]


class ObligationGate:
    """Obligation Gate: プラグイン化された Automated Gate 義務検証チェック群を実行する。"""

    def __init__(self, config: Config):
        self.config = config
        self.runner = GateRunner(
            checks=[
                AssessmentMissingCheck(),
                AssessmentIndependenceCheck(),
                VerificationTagSkippedCheck(),
                JudgeCoverageCheck(),
                DocumentJudgeCoverageCheck(),
            ]
        )

    def verify(
        self,
        documents: list[ParsedDocument],
        graph: Graph | None = None,
        db: DocAuditDB | None = None,
    ) -> tuple[list[VerificationIssue], ObligationSummary]:
        summary = ObligationSummary()
        if not self.config.obligation.enabled:
            return [], summary

        ctx = GateContext(
            documents=documents,
            graph=graph,
            docs_root=self.config.config_dir / self.config.project.docs_root,
            config=self.config,
            db=db,
            extra={"summary": summary},
        )
        issues = self.runner.run(ctx)
        return issues, summary
