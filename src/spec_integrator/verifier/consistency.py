from __future__ import annotations

import re
import json
import fnmatch
from pathlib import Path
from dataclasses import dataclass, field
from spec_integrator.config import Config
from spec_integrator.parser import ParsedDocument
from spec_integrator.verifier.static import VerificationIssue


# A value token: decimal, hex, or a size/unit-suffixed number.
VALUE_RE = re.compile(
    r"(?<![\w.])("
    r"0[xX][0-9a-fA-F][0-9a-fA-F_]*[uUlL]*"                     # 0x8000_0000 / 0x40000000U
    r"|\d[\d_]*(?:\.\d+)?\s*"
    r"(?:KB|kB|MB|Bytes|Byte|bytes|byte|キロバイト|バイト|bit|μs|us|ms|B)"  # 6.0 KB / 4096バイト
    r"|\d[\d_]*(?:\.\d+)?[uUlL]*"                                # 6144 / 6144U
    r")(?![0-9.])"
)

FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class SymbolDrift:
    symbol: str
    values: dict[str, list[str]] = field(default_factory=dict)  # normalized value -> ["file:line", ...]


@dataclass
class ConsistencySummary:
    invariants_checked: int = 0
    symbols_tracked: int = 0
    drifting_symbols: list[SymbolDrift] = field(default_factory=list)
    cochange_tracked: int = 0
    cochange_stale: list[dict] = field(default_factory=list)
    baseline_present: bool = False


def _normalize_value(raw: str) -> str:
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


class ConsistencyVerifier:
    """Consistency Gate.

    Targets the one failure mode that discipline does not fix: an edit that lands
    in one place and never reaches the other places that restate the same fact.

    Three independent mechanisms:
      A. Stale-value scan     — values you migrated away from must never reappear.
      B. Symbol drift         — one symbol must not carry conflicting values across files.
      C. Co-change staleness  — when the section defining a keyword changes, every
                                section that references it must be revisited.
    """

    def __init__(self, config: Config):
        self.config = config

    # ------------------------------------------------------------------ #
    def verify(self, documents: list[ParsedDocument], docs_root: Path
               ) -> tuple[list[VerificationIssue], ConsistencySummary]:
        summary = ConsistencySummary()
        cfg = self.config.consistency
        if not cfg.enabled:
            return [], summary

        issues: list[VerificationIssue] = []
        scanned = self._collect_scan_targets(documents, docs_root)

        issues.extend(self._check_stale_values(scanned, summary))
        issues.extend(self._check_symbol_drift(scanned, summary))
        issues.extend(self._check_cochange(documents, summary))
        return issues, summary

    # ------------------------------------------------------------------ #
    # Target collection: docs plus any extra roots (headers, sources)
    # ------------------------------------------------------------------ #
    def _collect_scan_targets(self, documents: list[ParsedDocument],
                              docs_root: Path) -> list[tuple[str, list[str]]]:
        targets: list[tuple[str, list[str]]] = []
        for doc in documents:
            targets.append((doc.file_path, doc.content.splitlines()))

        repo_root = self.config.config_dir
        seen = {t[0] for t in targets}
        for pattern in self.config.consistency.extra_scan_globs:
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

    # ------------------------------------------------------------------ #
    # A. Values you migrated away from must not reappear
    # ------------------------------------------------------------------ #
    def _check_stale_values(self, targets: list[tuple[str, list[str]]],
                            summary: ConsistencySummary) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        for inv in self.config.consistency.invariants:
            summary.invariants_checked += 1
            patterns = [(p, re.compile(p)) for p in inv.get("forbidden", [])]
            scope = inv.get("scope") or ["**/*"]
            exclude = inv.get("exclude") or []

            for rel_path, lines in targets:
                if not any(fnmatch.fnmatch(rel_path, s) for s in scope):
                    continue
                if any(fnmatch.fnmatch(rel_path, e) for e in exclude):
                    continue
                for idx, line in enumerate(lines, start=1):
                    for raw, rx in patterns:
                        if not rx.search(line):
                            continue
                        canonical = inv.get("canonical")
                        tail = f" 正: {canonical}" if canonical else ""
                        issues.append(VerificationIssue(
                            gate="Consistency", severity="ERROR",
                            file_path=rel_path, line=idx,
                            rule_code="CONSIST-STALE-VALUE",
                            message=(f"[{inv.get('id', 'invariant')}] superseded value matching "
                                     f"`{raw}` still present.{tail} — {inv.get('reason', '')}")
                        ))
        return issues

    # ------------------------------------------------------------------ #
    # B. One symbol, one value — zero configuration required
    # ------------------------------------------------------------------ #
    def _check_symbol_drift(self, targets: list[tuple[str, list[str]]],
                            summary: ConsistencySummary) -> list[VerificationIssue]:
        cfg = self.config.consistency
        if not cfg.symbol_patterns:
            return []

        symbol_res = [re.compile(p) for p in cfg.symbol_patterns]
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
        summary.symbols_tracked = len(observed)

        for symbol, values in sorted(observed.items()):
            # Values are only recorded from an unambiguous "this is the value"
            # position, so two distinct values for one symbol is real drift.
            if len(values) < 2:
                continue

            drift = SymbolDrift(symbol=symbol, values={v: list(l) for v, l in values.items()})
            summary.drifting_symbols.append(drift)

            rendered = "; ".join(
                f"{v} @ {', '.join(sorted(set(locs))[:3])}" for v, locs in sorted(values.items())
            )
            all_locs = sorted({l for locs in values.values() for l in locs})
            f_path, _, f_line = all_locs[0].rpartition(":")
            issues.append(VerificationIssue(
                gate="Consistency", severity="ERROR",
                file_path=f_path, line=int(f_line) if f_line.isdigit() else 1,
                rule_code="CONSIST-SYMBOL-DRIFT",
                message=(f"'{symbol}' carries conflicting values across the repository: {rendered}. "
                         "One fact must have one value; an edit reached some of these and not the others.")
            ))
        return issues

    @classmethod
    def _value_for_symbol(cls, line: str, symbol: str) -> str | None:
        """Extracts the value of `symbol` only from an unambiguous value position.

        Prose that merely mentions numbers near a symbol ("2KB x 3 = 6144 Bytes",
        "254 以下でなければならない") carries components and constraints, not the
        value, and must not be treated as a declaration. Three positions qualify:

          1. assignment      `SYMBOL = 6144U`
          2. default marker  `SYMBOL`: ... デフォルト値: 6144
          3. table cell      `| \\`SYMBOL\\` | description | \\`6144\\` |`
        """
        # 1. Assignment
        m = re.search(re.escape(symbol) + r"\s*(?:=|:=)\s*" + VALUE_RE.pattern, line)
        if m:
            return _normalize_value(m.group(1))

        # 2. Explicit default marker. The separator is required: "デフォルト値: 6144"
        #    declares a value, whereas "デフォルト 3面" counts banks.
        m = re.search(r"(?:デフォルト値?|既定値?|省略時|[Dd]efault)\s*[:：=]\s*" + VALUE_RE.pattern, line)
        if m:
            return _normalize_value(m.group(1))

        # 3. Markdown table row: exactly one cell that is nothing but a value
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # Only the row that *names* the symbol declares it. A symbol mentioned in
            # a later cell is a cross-reference, and the row's value belongs to
            # whatever that row is actually about.
            if not cells or symbol not in cells[0]:
                return None
            pure: list[str] = []
            for c in cells:
                if symbol in c:
                    continue
                token = c.strip().strip("`").strip()
                vm = VALUE_RE.fullmatch(token)
                if vm:
                    pure.append(_normalize_value(vm.group(1)))
            if len(pure) == 1:
                return pure[0]
        return None

    # ------------------------------------------------------------------ #
    # C. Co-change: a changed definition invalidates its references
    # ------------------------------------------------------------------ #
    def _check_cochange(self, documents: list[ParsedDocument],
                        summary: ConsistencySummary) -> list[VerificationIssue]:
        cfg = self.config.consistency
        if not cfg.cochange:
            return []

        lock_path = self.config.resolve_path(cfg.lockfile)
        baseline = self._load_lock(lock_path)
        summary.baseline_present = baseline is not None
        current = self.build_baseline(documents)

        if baseline is None:
            return [VerificationIssue(
                gate="Consistency", severity="WARNING",
                file_path=cfg.lockfile, line=1,
                rule_code="CONSIST-BASELINE-MISSING",
                message=("No consistency baseline recorded, so propagation of edits cannot be "
                         "checked. Run 'spec-integrator sync' to create it and commit the result.")
            )]

        if int(baseline.get("version", 1)) < 2:
            return [VerificationIssue(
                gate="Consistency", severity="WARNING",
                file_path=cfg.lockfile, line=1,
                rule_code="CONSIST-BASELINE-OUTDATED",
                message=("The consistency baseline predates keyword-level fingerprinting. "
                         "Run 'spec-integrator sync' to regenerate it.")
            )]

        old_secs = baseline.get("sections", {})
        new_secs = current["sections"]
        old_defs = baseline.get("definitions", {})
        new_defs = current["definitions"]
        new_refs = current["references"]

        changed = {sid for sid, h in new_secs.items() if old_secs.get(sid) not in (None, h)}
        summary.cochange_tracked = len(new_defs)

        issues: list[VerificationIssue] = []
        sec_index = {s.section_id: (d, s) for d in documents for s in d.sections}

        for keyword, definers in sorted(new_defs.items()):
            old_for_kw = old_defs.get(keyword) or {}
            # Only a definition that existed before can go stale; a brand-new
            # keyword has no downstream text to invalidate yet.
            if not old_for_kw:
                continue
            changed_definers = [sid for sid, fp in definers.items()
                                if sid in old_for_kw and old_for_kw[sid] != fp]
            if not changed_definers:
                continue

            for ref_id in sorted(new_refs.get(keyword, [])):
                if ref_id in changed:
                    continue          # revisited in the same edit — propagation happened
                if ref_id not in old_secs:
                    continue          # newly written section
                entry = sec_index.get(ref_id)
                if entry is None:
                    continue
                doc, sec = entry
                summary.cochange_stale.append({
                    "keyword": keyword,
                    "definer": changed_definers[0],
                    "referrer": ref_id,
                    "file_path": doc.file_path,
                    "heading": sec.heading,
                })
                issues.append(VerificationIssue(
                    gate="Consistency", severity="ERROR",
                    file_path=doc.file_path, line=sec.line_start,
                    rule_code="CONSIST-COCHANGE-STALE",
                    message=(f"The definition of '{{{keyword}}}' changed in "
                             f"'{changed_definers[0].replace('sec:', '')}', but this section still "
                             "carries the wording written against the previous definition. "
                             "Update it, or re-run 'spec-integrator sync' to accept it as unaffected.")
                ))
        return issues

    # ------------------------------------------------------------------ #
    @staticmethod
    def _definition_fingerprint(section_body: str, keyword: str) -> str:
        """Hashes only the lines that actually define the keyword.

        A requirements table holds dozens of definitions in one section. Hashing the
        whole section would make every edit invalidate every neighbouring keyword,
        so the unit of change has to be the definition itself, not its container.
        """
        from spec_integrator.parser import MarkdownParser
        token = "{" + keyword + "}"
        lines = [ln.strip() for ln in section_body.splitlines() if token in ln]
        return MarkdownParser.config_compute_hash("\n".join(lines))

    def build_baseline(self, documents: list[ParsedDocument]) -> dict:
        from spec_integrator.parser import MarkdownParser
        sections: dict[str, str] = {}
        definitions: dict[str, dict[str, str]] = {}
        references: dict[str, list[str]] = {}

        for doc in documents:
            for sec in doc.sections:
                sections[sec.section_id] = MarkdownParser.config_compute_hash(sec.body_text)
                for kw in set(sec.keywords):
                    if self.config.is_keyword_definition(kw, doc.file_path):
                        definitions.setdefault(kw, {})[sec.section_id] = \
                            self._definition_fingerprint(sec.body_text, kw)
                    else:
                        references.setdefault(kw, []).append(sec.section_id)

        return {
            "version": 2,
            "sections": sections,
            "definitions": definitions,
            "references": {k: sorted(set(v)) for k, v in references.items()},
        }

    def write_baseline(self, documents: list[ParsedDocument]) -> Path:
        lock_path = self.config.resolve_path(self.config.consistency.lockfile)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.build_baseline(documents)
        lock_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
                             encoding="utf-8")
        return lock_path

    @staticmethod
    def _load_lock(path: Path) -> dict | None:
        try:
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
