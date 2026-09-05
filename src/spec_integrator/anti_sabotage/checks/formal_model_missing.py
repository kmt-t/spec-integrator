from __future__ import annotations

from pathlib import Path

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import VerificationIssue


class FormalModelMissingCheck(AntiSabotageCheck):
    """形式モデルの欠落: {VERIFY_FORMAL} を宣言しているのに対応する formal/*.py が存在しない問題を検出する。"""

    rule_code = "FORMAL-MODEL-NOT-FOUND"
    name = "形式モデルの欠落"
    gate = "Formal"
    severity = "ERROR"
    description = "形式検証を宣言しながら検証スクリプトが存在しないサボりを検出する。"

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        cfg = ctx.config.formal_verification
        model_dir_name = cfg.model_dir_name
        tag = cfg.tag

        # 全モデルファイルの BACKS インデックスを作成
        all_model_files = sorted(ctx.docs_root.glob(f"**/{model_dir_name}/*.py"))
        all_model_files = [m for m in all_model_files if not m.name.startswith("_")]
        doc_to_backing: dict[str, list[Path]] = {}
        for mf in all_model_files:
            try:
                text = mf.read_text(encoding="utf-8", errors="replace")
                import re

                backs_match = re.search(r"BACKS\s*=\s*\[(.*?)\]", text, re.DOTALL)
                if backs_match:
                    items = re.findall(r"['\"]([^'\"]+)['\"]", backs_match.group(1))
                    for b in items:
                        doc_to_backing.setdefault(b, []).append(mf)
            except Exception:
                pass

        for doc in ctx.documents:
            if tag not in doc.all_tags:
                continue
            if doc.file_path in doc_to_backing:
                continue
            cur = doc.full_path.parent
            found = False
            while cur and cur != ctx.docs_root.parent and cur != ctx.docs_root:
                cand_dir = cur / model_dir_name
                if cand_dir.exists():
                    files = [m for m in cand_dir.glob("*.py") if not m.name.startswith("_")]
                    if files:
                        found = True
                        break
                cur = cur.parent
            if not found:
                rel_dir = (
                    (doc.full_path.parent / model_dir_name).relative_to(ctx.docs_root).as_posix()
                )
                issues.append(
                    VerificationIssue(
                        gate=self.gate,
                        severity=self.severity,
                        file_path=doc.file_path,
                        line=1,
                        rule_code=self.rule_code,
                        message=(
                            f"Document declares '{tag}' but no formal model script exists in "
                            f"'{rel_dir}/' or references it via BACKS. A verification claim "
                            "without a model is not admissible."
                        ),
                    )
                )
        return issues
