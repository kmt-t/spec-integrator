from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spec_integrator.config import Config
from spec_integrator.models import (
    FormalModelResult,
    ParsedDocument,
    VerificationIssue,
    WITFileResult,
)

if TYPE_CHECKING:
    from spec_integrator.db import DocAuditDB
    from spec_integrator.graph import Graph


@dataclass
class AntiSabotageContext:
    """Anti-Sabotage プラグインの検証実行コンテキスト。"""

    documents: list[ParsedDocument]
    graph: Graph | None
    docs_root: Path
    config: Config
    db: DocAuditDB | None = None
    formal_results: list[FormalModelResult] | None = None
    wit_results: list[WITFileResult] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # 高速参照用内部キャッシュ
    _doc_map: dict[str, ParsedDocument] | None = field(default=None, init=False, repr=False)

    @property
    def doc_map(self) -> dict[str, ParsedDocument]:
        if self._doc_map is None:
            self._doc_map = {d.file_path: d for d in self.documents}
        return self._doc_map


class AntiSabotageCheck(ABC):
    """標準化された Anti-Sabotage プラグイン基底クラス。"""

    rule_code: str
    name: str  # 正規化名: 「〔対象〕の〔問題種別〕」（例: "リンク先の欠落"）
    gate: str  # "Format", "Formal", "Evidence", "Obligation", "Consistency"
    severity: str = "ERROR"  # "ERROR" | "WARNING"
    description: str = ""

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        """設定やコンテキストに基づいてこのチェックが有効か判定する。"""
        return True

    @abstractmethod
    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        """検証を実行し、検出した違反 (VerificationIssue) のリストを返す。"""
        ...
