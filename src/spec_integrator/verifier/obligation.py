from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageContext
from spec_integrator.anti_sabotage.checks.oblig_assessment_independence import (
    AssessmentIndependenceCheck,
)
from spec_integrator.anti_sabotage.checks.oblig_assessment_missing import (
    AssessmentMissingCheck,
)
from spec_integrator.anti_sabotage.checks.oblig_doc_judge_coverage import (
    DocumentJudgeCoverageCheck,
)
from spec_integrator.anti_sabotage.checks.oblig_judge_coverage import JudgeCoverageCheck
from spec_integrator.anti_sabotage.checks.oblig_verification_skipped import (
    VerificationTagSkippedCheck,
)
from spec_integrator.anti_sabotage.runner import AntiSabotageRunner
from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.graph import Graph
from spec_integrator.models import ObligationSummary, ParsedDocument, VerificationIssue

__all__ = ["ObligationSummary", "ObligationVerifier"]


class ObligationVerifier:
    """Obligation Gate: プラグイン化された Anti-Sabotage 義務検証チェック群を実行する。"""

    def __init__(self, config: Config):
        self.config = config
        self.runner = AntiSabotageRunner(
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

        ctx = AntiSabotageContext(
            documents=documents,
            graph=graph,
            docs_root=self.config.config_dir / self.config.project.docs_root,
            config=self.config,
            db=db,
            extra={"summary": summary},
        )
        issues = self.runner.run(ctx)
        return issues, summary
