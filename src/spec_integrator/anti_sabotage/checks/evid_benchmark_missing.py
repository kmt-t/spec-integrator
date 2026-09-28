from __future__ import annotations

from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class BenchmarkScriptMissingCheck(AntiSabotageCheck):
    """ベンチマーク実装の欠落: {VERIFY_BENCHMARK} を宣言しているのに benchmarks/*.py が存在しない問題を検出する。"""

    rule_code = "EVID-BENCHMARK-MISSING"
    name = "ベンチマーク実装の欠落"
    gate = "Evidence"
    severity = "ERROR"
    description = "計測値を主張しながら実測ベンチマークコードが存在しないサボりを検出する。"

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.evidence.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        cfg = ctx.config.benchmark_verification
        repo_root = ctx.config.config_dir
        for doc in ctx.documents:
            if cfg.tag not in doc.all_tags:
                continue
            benchmark_ref = doc.evidence.get("benchmark")
            if benchmark_ref:
                doc_dir = (ctx.docs_root / doc.file_path).parent
                benchmark_candidates = [
                    doc_dir / benchmark_ref,
                    ctx.docs_root / benchmark_ref,
                    repo_root / benchmark_ref,
                ]
                if any(
                    candidate.is_file() and candidate.suffix == ".py"
                    for candidate in benchmark_candidates
                ):
                    continue
            bench_dir = ctx.docs_root / Path(doc.file_path).parent / cfg.benchmark_dir_name
            if bench_dir.is_dir() and any(bench_dir.glob("*.py")):
                continue
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=doc.file_path,
                    line=1,
                    rule_code=self.rule_code,
                    message=(
                        f"Document declares '{cfg.tag}' but its benchmark evidence does not "
                        "resolve to a Python script and no local benchmark script exists under "
                        f"'{Path(doc.file_path).parent}/{cfg.benchmark_dir_name}/'. An empirical "
                        "claim needs a real, runnable measurement, not just the tag."
                    ),
                )
            )
        return issues
