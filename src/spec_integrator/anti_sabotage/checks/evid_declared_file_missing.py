from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class DeclaredEvidenceFileMissingCheck(AntiSabotageCheck):
    """証跡ファイルの欠落: <!-- evidence: ... --> に書かれたパスが実在するか検証する。"""

    rule_code = "EVID-DECLARED-FILE-MISSING"
    name = "証跡ファイルの欠落"
    gate = "Evidence"
    severity = "ERROR"
    description = "ドキュメント内で宣言された証跡ファイルがディスク上に存在しない問題を検出する。"

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.evidence.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        repo_root = ctx.config.config_dir
        for doc in ctx.documents:
            doc_dir = (ctx.docs_root / doc.file_path).parent
            for ev_type, ev_path in doc.evidence.items():
                resolved = None
                for cand in [doc_dir / ev_path, ctx.docs_root / ev_path, repo_root / ev_path]:
                    if cand.exists():
                        resolved = cand
                        break
                if resolved is None:
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=doc.file_path,
                            line=1,
                            rule_code=self.rule_code,
                            message=(
                                f"Declared evidence '{ev_type}: {ev_path}' does not exist on disk. "
                                "Check the relative path from the document."
                            ),
                        )
                    )
        return issues
