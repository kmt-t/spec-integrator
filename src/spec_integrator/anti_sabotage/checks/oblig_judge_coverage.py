from __future__ import annotations

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class JudgeCoverageCheck(AntiSabotageCheck):
    """意味監査結果の検証: {VERIFY_LLM} を持つドキュメントの意味監査実施・固定・網羅・合否を検証する。"""

    rule_code = "OBLIG-JUDGE-MISSING"
    name = "意味監査結果の欠落・未固定・漏れ・不合格"
    gate = "Obligation"
    severity = "ERROR"
    description = "セマンティック監査の未実施、判定結果の未アンカー、監査対象からの漏れ、FAIL 判定の放置を検出する。"

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

        entries = db.get_judge_results()
        hashes = db.get_assessed_doc_hashes("judge")
        summary = ctx.extra.get("summary")

        if not entries:
            if summary is not None:
                summary.judge_missing.extend(d.file_path for d in tagged)
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=str(ctx.config.get_db_path()),
                    line=1,
                    rule_code="OBLIG-JUDGE-MISSING",
                    message=(
                        f"{len(tagged)} document(s) carry '{llm_tag}' but the database contains "
                        "no LLM judge verdict. Run 'spec-integrator llm-judge' to audit the "
                        "semantic consistency of these specifications."
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
                    rule_code="OBLIG-JUDGE-UNANCHORED",
                    message=(
                        "The LLM judge verdict records no document hashes, so there is no way "
                        "to tell which version of the specification it audited. Re-run "
                        "'spec-integrator llm-judge' to produce an anchored verdict."
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
                            rule_code="OBLIG-JUDGE-STALE",
                            message=(
                                f"Document declares '{llm_tag}' but has changed since the LLM judge "
                                "audited it. The stored verdict describes an earlier version of this text — "
                                "re-run 'spec-integrator llm-judge'."
                            ),
                        )
                    )

        covered: set[str] = set()
        for e in entries:
            covered.update(e.get("covered_files", []) or [])

        for doc in tagged:
            if doc.file_path not in covered:
                if summary is not None:
                    summary.judge_missing.append(doc.file_path)
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=1,
                        rule_code="OBLIG-JUDGE-SKIPPED",
                        message=(
                            f"Document declares '{llm_tag}' but does not appear in the LLM judge verdict. "
                            "Raise --max-subgraphs so the audit actually covers it."
                        ),
                    )
                )

        failed_keywords = {
            e.get("item_label", "").strip("{}") for e in entries if e.get("status") == "FAIL"
        }
        for doc in tagged:
            if doc.file_path not in covered:
                continue
            for kw in sorted(failed_keywords & set(doc.all_keywords)):
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=1,
                        rule_code="OBLIG-JUDGE-FAILED",
                        message=(
                            f"LLM semantic audit reported FAIL for '{{{kw}}}', which this document "
                            "cites, in the stored judge verdict."
                        ),
                    )
                )

        return issues
