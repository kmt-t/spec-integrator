from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class DocumentJudgeCoverageCheck(AntiSabotageCheck):
    """文書単位監査結果の検証: {VERIFY_LLM} を持つドキュメントの文書単位自己一貫性監査を検証する。"""

    rule_code = "OBLIG-DOC-JUDGE-MISSING"
    name = "文書単位監査結果の欠落・未固定・漏れ・不合格"
    gate = "Obligation"
    severity = "ERROR"
    description = "文書単位のセマンティック自己整合性監査の未実施、ハッシュ未固定、監査漏れ、FAIL 判定の放置を検出する。"

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.obligation.enabled and ctx.config.obligation.require_judge

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        db = ctx.db
        if db is None:
            return issues

        llm_tag = ctx.config.llm_judge.tag
        tagged = [d for d in ctx.documents if llm_tag in d.all_tags]
        if not tagged:
            return []

        entries = db.get_document_judge_results()
        hashes = db.get_assessed_doc_hashes("document_judge")
        summary = ctx.extra.get("summary")

        if not entries:
            if summary is not None:
                summary.document_judge_missing.extend(d.file_path for d in tagged)
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=str(ctx.config.get_db_path()),
                    line=1,
                    rule_code="OBLIG-DOC-JUDGE-MISSING",
                    message=(
                        f"{len(tagged)} document(s) carry '{llm_tag}' but the database contains "
                        "no whole-document LLM judge verdict. Run 'spec-integrator llm-judge' "
                        "to audit the internal consistency of each document."
                    ),
                )
            )
            return issues

        if not hashes:
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=str(ctx.config.get_db_path()),
                    line=1,
                    rule_code="OBLIG-DOC-JUDGE-UNANCHORED",
                    message=(
                        "The whole-document LLM judge verdict records no document hashes, "
                        "so there is no way to tell which version of the specification it audited. "
                        "Re-run 'spec-integrator llm-judge' to produce an anchored verdict."
                    ),
                )
            )
            return issues

        if ctx.config.obligation.stale_is_error:
            for doc in tagged:
                rec = hashes.get(doc.file_path)
                if rec is not None and rec != doc.content_hash:
                    issues.append(
                        VerificationIssue(
                            gate=self.gate,
                            severity=self.severity,
                            file_path=doc.file_path,
                            line=1,
                            rule_code="OBLIG-DOC-JUDGE-STALE",
                            message=(
                                f"Document declares '{llm_tag}' but has changed since the "
                                "whole-document LLM judge audited it. The stored verdict describes an "
                                "earlier version of this text — re-run 'spec-integrator llm-judge'."
                            ),
                        )
                    )

        covered: set[str] = set()
        for e in entries:
            covered.update(e.get("covered_files", []) or [])

        for doc in tagged:
            if doc.file_path not in covered:
                if summary is not None:
                    summary.document_judge_missing.append(doc.file_path)
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=1,
                        rule_code="OBLIG-DOC-JUDGE-SKIPPED",
                        message=(
                            f"Document declares '{llm_tag}' but does not appear in the whole-document "
                            "LLM judge verdict. Raise --max-documents so the audit actually covers it."
                        ),
                    )
                )

        failed_docs = {e["item_id"] for e in entries if e.get("status") == "FAIL"}
        for doc in tagged:
            if doc.file_path in covered and doc.file_path in failed_docs:
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=1,
                        rule_code="OBLIG-DOC-JUDGE-FAILED",
                        message=(
                            "The whole-document LLM semantic audit reported FAIL for this document "
                            "in the stored judge verdict."
                        ),
                    )
                )

        return issues
