from __future__ import annotations

import re

from spec_integrator.anti_sabotage.base import AntiSabotageCheck, AntiSabotageContext
from spec_integrator.models import SymbolDrift, VerificationIssue

VALUE_RE = re.compile(
    r"(?<![\w.])("
    r"0[xX][0-9a-fA-F][0-9a-fA-F_]*[uUlL]*"  # 0x8000_0000 / 0x40000000U
    r"|\d[\d_]*(?:\.\d+)?\s*"
    r"(?:KB|kB|MB|Bytes|Byte|bytes|byte|キロバイト|バイト|bit|μs|us|ms|B)"  # 6.0 KB / 4096バイト
    r"|\d[\d_]*(?:\.\d+)?[uUlL]*"  # 6144 / 6144U
    r")(?![0-9.])"
)

FENCE_RE = re.compile(r"^\s*(```|~~~)")


def normalize_value(raw: str) -> str:
    """Collapses spellings of the same quantity so 6144 / 6.0KB / 6 KB compare equal."""
    s = raw.strip().replace("_", "").replace(" ", "").replace("　", "")
    m = re.fullmatch(r"(?i)0x([0-9a-f]+)[ul]*", s)
    if m:
        return str(int(m.group(1), 16))
    m = re.fullmatch(r"(?i)(\d+(?:\.\d+)?)(kb|mb|bytes?|キロバイト|バイト|b|bit|μs|us|ms)", s)
    if m:
        num, unit = float(m.group(1)), m.group(2).lower()
        if unit in ("kb", "キロバイト"):
            return str(int(num * 1024))
        if unit == "mb":
            return str(int(num * 1024 * 1024))
        if unit in ("byte", "bytes", "バイト", "b"):
            return str(int(num))
        return f"{int(num) if num.is_integer() else num}{unit}"
    s = re.sub(r"(?i)[ul]+$", "", s)
    m = re.fullmatch(r"\d+(?:\.\d+)?", s)
    if m:
        f = float(s)
        return str(int(f)) if f.is_integer() else s
    return s


class SymbolDriftCheck(AntiSabotageCheck):
    """シンボル値の不一致: 同一シンボルがリポジトリ内の複数箇所で異なる値を持つ問題を検出する。"""

    rule_code = "CONSIST-SYMBOL-DRIFT"
    name = "シンボル値の不一致"
    gate = "Consistency"
    severity = "ERROR"
    description = (
        "設定定数や構成シンボルの定義値がドキュメントやコード間で乖離している問題を検出する。"
    )

    def is_enabled(self, ctx: AntiSabotageContext) -> bool:
        return ctx.config.consistency.enabled

    def check(self, ctx: AntiSabotageContext) -> list[VerificationIssue]:
        cfg = ctx.config.consistency
        if not cfg.symbol_patterns:
            return []
        symbol_res = [re.compile(p) for p in cfg.symbol_patterns]
        targets = self._collect_scan_targets(ctx)

        # symbol -> normalized value -> list of "file:line"
        observed: dict[str, dict[str, list[str]]] = {}
        for rel_path, lines in targets:
            in_code = False
            for idx, line in enumerate(lines, start=1):
                if FENCE_RE.match(line):
                    in_code = not in_code
                    continue
                if in_code:
                    continue
                names: set[str] = set()
                for rx in symbol_res:
                    names |= {m.group(0) for m in rx.finditer(line)}
                if not names:
                    continue
                for name in names:
                    value = self._value_for_symbol(line, name)
                    if value is None:
                        continue
                    observed.setdefault(name, {}).setdefault(value, []).append(f"{rel_path}:{idx}")

        issues: list[VerificationIssue] = []
        if "summary" in ctx.extra and hasattr(ctx.extra["summary"], "symbols_tracked"):
            ctx.extra["summary"].symbols_tracked = len(observed)

        for symbol, values in sorted(observed.items()):
            if len(values) < 2:
                continue
            drift = SymbolDrift(symbol=symbol, values={v: list(l) for v, l in values.items()})
            if "summary" in ctx.extra and hasattr(ctx.extra["summary"], "drifting_symbols"):
                ctx.extra["summary"].drifting_symbols.append(drift)
            rendered = "; ".join(
                f"{v} @ {', '.join(sorted(set(locs))[:3])}" for v, locs in sorted(values.items())
            )
            all_locs = sorted({l for locs in values.values() for l in locs})
            f_path, _, f_line = all_locs[0].rpartition(":")
            issues.append(
                VerificationIssue(
                    gate=self.gate,
                    severity=self.severity,
                    file_path=f_path,
                    line=int(f_line) if f_line.isdigit() else 1,
                    rule_code=self.rule_code,
                    message=(
                        f"'{symbol}' carries conflicting values across the repository: {rendered}. "
                        "One fact must have one value; an edit reached some of these and not the others."
                    ),
                )
            )
        return issues

    def _collect_scan_targets(self, ctx: AntiSabotageContext) -> list[tuple[str, list[str]]]:
        targets: list[tuple[str, list[str]]] = []
        for doc in ctx.documents:
            targets.append((doc.file_path, doc.content.splitlines()))

        repo_root = ctx.config.config_dir
        seen = {t[0] for t in targets}
        for pattern in ctx.config.consistency.extra_scan_globs:
            for p in sorted(repo_root.glob(pattern)):
                if not p.is_file():
                    continue
                try:
                    rel = p.relative_to(repo_root).as_posix()
                except ValueError:
                    rel = p.as_posix()
                if rel in seen:
                    continue
                try:
                    targets.append((rel, p.read_text(encoding="utf-8").splitlines()))
                    seen.add(rel)
                except (OSError, UnicodeDecodeError):
                    continue
        return targets

    @classmethod
    def _value_for_symbol(cls, line: str, symbol: str) -> str | None:
        # 1. Assignment
        m = re.search(re.escape(symbol) + r"\s*(?:=|:=)\s*" + VALUE_RE.pattern, line)
        if m:
            return normalize_value(m.group(1))
        # 2. Explicit default marker
        m = re.search(
            r"(?:デフォルト値?|既定値?|省略時|[Dd]efault)\s*[:：=]\s*" + VALUE_RE.pattern,
            line,
        )
        if m:
            return normalize_value(m.group(1))
        # 3. Markdown table row: exactly one cell that is nothing but a value
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not cells or symbol not in cells[0]:
                return None
            pure: list[str] = []
            for c in cells:
                if symbol in c:
                    continue
                token = c.strip().strip("`").strip()
                vm = VALUE_RE.fullmatch(token)
                if vm:
                    pure.append(normalize_value(vm.group(1)))
            if len(pure) == 1:
                return pure[0]
        return None
