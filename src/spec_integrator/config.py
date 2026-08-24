from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
import yaml


def regex_match(pattern: str, rel_path: str) -> bool:
    """Matches a relative path against a regular expression pattern."""
    norm_path = rel_path.replace("\\", "/").lstrip("./")
    try:
        if re.search(pattern, norm_path):
            return True
        # Match with or without leading docs/
        if not norm_path.startswith("docs/"):
            if re.search(pattern, f"docs/{norm_path}"):
                return True
        else:
            if re.search(pattern, norm_path[5:]):
                return True
    except re.error:
        pass
    return False


@dataclass
class TierConfig:
    tier: int | str  # 0, 1, 2, 3 or "meta"
    name: str
    path_pattern: str  # Regular expression pattern
    description: str = ""

    def matches(self, rel_path: str) -> bool:
        """Checks if a relative path matches the tier's regex pattern."""
        return regex_match(self.path_pattern, rel_path)


@dataclass
class KeywordRule:
    pattern: str       # Regular expression pattern for keyword syntax (e.g. ^REQ_[A-Z0-9_]+$)
    defined_in: str    # Regular expression pattern for definition file path (e.g. requires/.*\.md)
    description: str = ""

    def is_definition_file(self, rel_path: str) -> bool:
        """Checks if the file matches the regex pattern for where the keyword is defined."""
        return regex_match(self.defined_in, rel_path)


@dataclass
class FormalVerificationConfig:
    model_dir_name: str = "formal"
    tag: str = "{VERIFY_FORMAL}"
    timeout_seconds: int = 30
    # --- Anti-vacuity audit (a model that cannot fail is not a proof) ---
    require_contract: bool = True     # model must expose build_model() + properties()
    check_vacuity: bool = True        # safety property whose violation state is unrepresentable => NG
    check_reachability: bool = True   # states unreachable from S0 => NG
    check_nondeterminism: bool = True # single-path model cannot exhibit interleaving => NG
    min_states: int = 4               # models smaller than this cannot back a concurrency claim
    # Mutation test: disabling the guard must make the violation reachable, otherwise
    # the safety property holds because the transition was omitted, not prevented.
    check_guard_effectiveness: bool = True


@dataclass
class EvidenceConfig:
    """Evidence Gate: forbids asserting a verification that was never performed."""
    enabled: bool = True
    claim_patterns: list[str] = field(default_factory=lambda: [
        "検証済み", "検証されている", "検証を実施", "検証済である",
        "証明完了", "証明済み", "証明した", "数学的証明",
        "実施済み", "立証",
        "formally verified", "has been verified", "is verified",
        "proven", "proved", "mathematically proven",
    ])
    measurement_patterns: list[str] = field(default_factory=lambda: [
        "測定環境", "実測値", "実測", "計測結果", "ベンチマーク結果",
        "measured on", "benchmark result", "measurement environment",
    ])
    artifact_extensions: list[str] = field(default_factory=lambda: [
        "py", "md", "wit", "tla", "json", "cfg",
    ])
    # Unsourced bare metrics (percentages / cycle counts) — noisy, so WARNING by default.
    metric_severity: str = "WARNING"
    ignore_artifact_refs: list[str] = field(default_factory=list)


@dataclass
class ConsistencyConfig:
    """Consistency Gate: an edit must reach every place that restates the same fact."""
    enabled: bool = True
    lockfile: str = "spec-consistency.lock"
    cochange: bool = True
    # Symbols whose value must be identical everywhere it appears. Zero-config drift
    # detection: no registry to maintain, the symbol name is the key.
    symbol_patterns: list[str] = field(default_factory=lambda: [
        r"\bFB_CONF_[A-Z0-9_]+\b",
        r"\bFB_[A-Z0-9_]+_(?:MAX|MIN|SIZE|BASE|LIMIT)\b",
    ])
    # Extra (non-Markdown) files to include, e.g. the configuration header.
    extra_scan_globs: list[str] = field(default_factory=lambda: [
        "inc/**/*.hxx", "inc/*.hxx", "src/**/*.hxx",
    ])
    # Values a migration moved away from, which must never reappear.
    invariants: list[dict] = field(default_factory=list)


@dataclass
class ObligationConfig:
    """Obligation Gate: verification demanded by the risk assessment must not be skipped."""
    enabled: bool = True
    risk_report: str = "reports/doc_risk_report.json"
    judge_report: str = "reports/doc_judge_report.json"
    require_assessment: bool = True   # no risk assessment at all => NG (this is the "サボり" case)
    require_judge: bool = True        # {VERIFY_LLM} tagged but never judged => NG
    risk_threshold: int = 4           # risk_score >= threshold demands the recommended verification
    stale_is_error: bool = True       # doc changed since it was assessed => NG
    # A discharge rate over a partial assessment overstates coverage => NG
    require_full_coverage: bool = True
    # Backends whose verdict is derived from the artifact under test, not independent of it
    forbidden_backends: list[str] = field(default_factory=lambda: ["mock"])


@dataclass
class WITVerificationConfig:
    wit_dir_name: str = "wit"
    tag: str = "{VERIFY_WIT}"


@dataclass
class LLMBackendConfig:
    api_key_env: str = "SAKURA_API_KEY"
    endpoint: str = "http://localhost:11434"
    model: str = ""


@dataclass
class LLMJudgeConfig:
    tag: str = "{VERIFY_LLM}"
    default_backend: str = "sakura"
    backends: dict[str, LLMBackendConfig] = field(default_factory=dict)
    # Characters of each section handed to the judge. Truncation is reported to
    # the model rather than applied silently: a verdict of "no contradiction"
    # drawn from text the judge never saw is worse than no verdict at all.
    section_char_budget: int = 8000


@dataclass
class ProjectConfig:
    name: str = "Spec Project"
    docs_root: str = "docs"
    cache_db: str = ".spec-integrator/doc_cache.db"
    exclude_patterns: list[str] = field(default_factory=lambda: ["**/FORMAT.md", "FORMAT.md"])


@dataclass
class WaiverRule:
    """Explicitly justified waiver for a section or document from verification obligations."""
    section_pattern: str = ""
    heading_pattern: str = ""
    rationale: str = ""
    authorized_at: str = ""

    def matches(self, file_path: str, heading: str) -> bool:
        if self.section_pattern and not regex_match(self.section_pattern, file_path):
            return False
        if self.heading_pattern and not re.search(self.heading_pattern, heading):
            return False
        return True


@dataclass
class HeuristicConfig:
    """Configuration-driven keyword triggers and scope rules for independent risk assessment."""
    # No hardcoded word lists here: `formal_triggers` / `llm_triggers` /
    # `non_formal_path_patterns` are project-specific vocabulary, not tool
    # behavior, so spec-integrator.yaml is their one and only source. A
    # second copy baked into this dataclass would drift from the YAML the
    # moment either one is edited without the other (as `llm_triggers` and
    # `non_formal_path_patterns` already had, silently, until this fix).
    formal_triggers: list[str] = field(default_factory=list)
    llm_triggers: list[str] = field(default_factory=list)
    non_formal_tiers: list[int | str] = field(default_factory=lambda: [0, "meta"])
    non_formal_path_patterns: list[str] = field(default_factory=list)
    waivers: list[WaiverRule] = field(default_factory=list)


@dataclass
class RiskAssessmentConfig:
    heuristic: HeuristicConfig = field(default_factory=HeuristicConfig)


@dataclass
class Config:
    version: str = "1.0"
    project: ProjectConfig = field(default_factory=ProjectConfig)
    tiers: list[TierConfig] = field(default_factory=list)
    keywords: dict[str, KeywordRule] = field(default_factory=dict)
    formal_verification: FormalVerificationConfig = field(default_factory=FormalVerificationConfig)
    wit_verification: WITVerificationConfig = field(default_factory=WITVerificationConfig)
    llm_judge: LLMJudgeConfig = field(default_factory=LLMJudgeConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    obligation: ObligationConfig = field(default_factory=ObligationConfig)
    consistency: ConsistencyConfig = field(default_factory=ConsistencyConfig)
    risk_assessment: RiskAssessmentConfig = field(default_factory=RiskAssessmentConfig)
    config_dir: Path = field(default_factory=Path.cwd)

    def is_excluded(self, file_path: str | Path, docs_root: Path | None = None) -> bool:
        """Checks if a file path matches any exclusion patterns."""
        import fnmatch
        p_str = str(file_path).replace("\\", "/").lstrip("./")
        if docs_root:
            try:
                p_rel = Path(file_path).relative_to(docs_root).as_posix().lstrip("./")
            except ValueError:
                p_rel = p_str
        else:
            p_rel = p_str

        file_name = Path(p_str).name

        for pat in self.project.exclude_patterns:
            norm_pat = pat.replace("\\", "/").lstrip("./")
            # 1. Direct filename match (e.g. FORMAT.md)
            if file_name == norm_pat or norm_pat == f"**/{file_name}":
                return True
            # 2. Glob match
            if fnmatch.fnmatch(p_rel, norm_pat) or fnmatch.fnmatch(p_str, norm_pat) or fnmatch.fnmatch(file_name, norm_pat):
                return True
            # 3. Regex match
            if regex_match(norm_pat, p_rel) or regex_match(norm_pat, p_str):
                return True
        return False

    @classmethod
    def load(cls, config_path: str | Path) -> Config:
        path = Path(config_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config_dir = path.parent

        # Project
        proj_data = data.get("project", {})
        default_excludes = ["**/FORMAT.md", "FORMAT.md"]
        custom_excludes = proj_data.get("exclude_patterns", [])
        combined_excludes = list(dict.fromkeys(default_excludes + custom_excludes))

        project = ProjectConfig(
            name=proj_data.get("name", "Spec Project"),
            docs_root=proj_data.get("docs_root", "docs"),
            cache_db=proj_data.get("cache_db", ".spec-integrator/doc_cache.db"),
            exclude_patterns=combined_excludes
        )

        # Tiers
        tiers = []
        for t in data.get("tiers", []):
            tier_val = t.get("tier")
            if isinstance(tier_val, str) and tier_val.isdigit():
                tier_val = int(tier_val)
            tiers.append(TierConfig(
                tier=tier_val,
                name=t.get("name", ""),
                path_pattern=t.get("path_pattern", ""),
                description=t.get("description", "")
            ))

        # Keywords
        keywords = {}
        for k_type, k_data in data.get("keywords", {}).items():
            keywords[k_type] = KeywordRule(
                pattern=k_data.get("pattern", ""),
                defined_in=k_data.get("defined_in", ""),
                description=k_data.get("description", "")
            )

        # Formal
        fv_data = data.get("formal_verification", {})
        fv_defaults = FormalVerificationConfig()
        formal_verification = FormalVerificationConfig(
            model_dir_name=fv_data.get("model_dir_name", "formal"),
            tag=fv_data.get("tag", "{VERIFY_FORMAL}"),
            timeout_seconds=fv_data.get("timeout_seconds", 30),
            require_contract=bool(fv_data.get("require_contract", fv_defaults.require_contract)),
            check_vacuity=bool(fv_data.get("check_vacuity", fv_defaults.check_vacuity)),
            check_reachability=bool(fv_data.get("check_reachability", fv_defaults.check_reachability)),
            check_nondeterminism=bool(fv_data.get("check_nondeterminism", fv_defaults.check_nondeterminism)),
            min_states=int(fv_data.get("min_states", fv_defaults.min_states)),
            check_guard_effectiveness=bool(fv_data.get(
                "check_guard_effectiveness", fv_defaults.check_guard_effectiveness)),
        )

        # Evidence Gate
        ev_data = data.get("evidence", {})
        ev_defaults = EvidenceConfig()
        evidence = EvidenceConfig(
            enabled=bool(ev_data.get("enabled", ev_defaults.enabled)),
            claim_patterns=list(ev_data.get("claim_patterns", ev_defaults.claim_patterns)),
            measurement_patterns=list(ev_data.get("measurement_patterns", ev_defaults.measurement_patterns)),
            artifact_extensions=list(ev_data.get("artifact_extensions", ev_defaults.artifact_extensions)),
            metric_severity=str(ev_data.get("metric_severity", ev_defaults.metric_severity)).upper(),
            ignore_artifact_refs=list(ev_data.get("ignore_artifact_refs", ev_defaults.ignore_artifact_refs)),
        )

        # Obligation Gate
        ob_data = data.get("obligation", {})
        ob_defaults = ObligationConfig()
        obligation = ObligationConfig(
            enabled=bool(ob_data.get("enabled", ob_defaults.enabled)),
            risk_report=str(ob_data.get("risk_report", ob_defaults.risk_report)),
            judge_report=str(ob_data.get("judge_report", ob_defaults.judge_report)),
            require_assessment=bool(ob_data.get("require_assessment", ob_defaults.require_assessment)),
            require_judge=bool(ob_data.get("require_judge", ob_defaults.require_judge)),
            risk_threshold=int(ob_data.get("risk_threshold", ob_defaults.risk_threshold)),
            stale_is_error=bool(ob_data.get("stale_is_error", ob_defaults.stale_is_error)),
            require_full_coverage=bool(ob_data.get(
                "require_full_coverage", ob_defaults.require_full_coverage)),
            forbidden_backends=list(ob_data.get(
                "forbidden_backends", ob_defaults.forbidden_backends)),
        )

        # WIT
        wit_data = data.get("wit_verification", {})
        wit_verification = WITVerificationConfig(
            wit_dir_name=wit_data.get("wit_dir_name", "wit"),
            tag=wit_data.get("tag", "{VERIFY_WIT}")
        )

        # LLM
        llm_data = data.get("llm_judge", {})
        backends = {}
        for b_name, b_info in llm_data.get("backends", {}).items():
            backends[b_name] = LLMBackendConfig(
                api_key_env=b_info.get("api_key_env", ""),
                endpoint=b_info.get("endpoint", ""),
                model=b_info.get("model", "")
            )

        llm_judge = LLMJudgeConfig(
            tag=llm_data.get("tag", "{VERIFY_LLM}"),
            default_backend=llm_data.get("default_backend", "sakura"),
            backends=backends
        )

        # Consistency Gate
        cs_data = data.get("consistency", {})
        cs_defaults = ConsistencyConfig()
        invariants = list(cs_data.get("invariants", cs_defaults.invariants))
        inv_file = cs_data.get("invariants_file")
        if inv_file:
            inv_path = (config_dir / inv_file) if not Path(inv_file).is_absolute() else Path(inv_file)
            if inv_path.exists():
                with open(inv_path, "r", encoding="utf-8") as f:
                    extra = yaml.safe_load(f) or {}
                invariants = invariants + list(extra.get("invariants", []))

        consistency = ConsistencyConfig(
            enabled=bool(cs_data.get("enabled", cs_defaults.enabled)),
            lockfile=str(cs_data.get("lockfile", cs_defaults.lockfile)),
            cochange=bool(cs_data.get("cochange", cs_defaults.cochange)),
            symbol_patterns=list(cs_data.get("symbol_patterns", cs_defaults.symbol_patterns)),
            extra_scan_globs=list(cs_data.get("extra_scan_globs", cs_defaults.extra_scan_globs)),
            invariants=invariants,
        )

        # Risk Assessment (Heuristic Engine & Waivers)
        ra_data = data.get("risk_assessment", {})
        h_data = ra_data.get("heuristic", {})
        h_defaults = HeuristicConfig()

        waivers = []
        for w in h_data.get("waivers", []):
            waivers.append(WaiverRule(
                section_pattern=w.get("section_pattern", ""),
                heading_pattern=w.get("heading_pattern", ""),
                rationale=w.get("rationale", ""),
                authorized_at=w.get("authorized_at", "")
            ))

        heuristic_config = HeuristicConfig(
            formal_triggers=list(h_data.get("formal_triggers", h_defaults.formal_triggers)),
            llm_triggers=list(h_data.get("llm_triggers", h_defaults.llm_triggers)),
            non_formal_tiers=list(h_data.get("non_formal_tiers", h_defaults.non_formal_tiers)),
            non_formal_path_patterns=list(h_data.get("non_formal_path_patterns", h_defaults.non_formal_path_patterns)),
            waivers=waivers if "waivers" in h_data else h_defaults.waivers
        )
        risk_assessment = RiskAssessmentConfig(heuristic=heuristic_config)

        return cls(
            version=str(data.get("version", "1.0")),
            project=project,
            tiers=tiers,
            keywords=keywords,
            formal_verification=formal_verification,
            wit_verification=wit_verification,
            llm_judge=llm_judge,
            evidence=evidence,
            obligation=obligation,
            consistency=consistency,
            risk_assessment=risk_assessment,
            config_dir=config_dir
        )

    def resolve_path(self, rel_or_abs: str | Path) -> Path:
        """Resolves a path declared in the config relative to the config file's directory."""
        p = Path(rel_or_abs)
        if p.is_absolute():
            return p
        return (self.config_dir / p).resolve()

    def get_tier_for_path(self, rel_path: str) -> int | str | None:
        """Determines the tier of a given file path based on configured regex patterns."""
        for t in self.tiers:
            if t.matches(rel_path):
                return t.tier
        return None

    def is_keyword_definition(self, keyword: str, file_path: str) -> bool:
        """Checks if a given file_path is the definition source for the keyword."""
        for k_type, rule in self.keywords.items():
            if re.match(rule.pattern, keyword):
                if rule.is_definition_file(file_path):
                    return True
        return False

    def get_docs_dir(self) -> Path:
        return (self.config_dir / self.project.docs_root).resolve()

    def get_db_path(self) -> Path:
        db_p = Path(self.project.cache_db)
        if db_p.is_absolute():
            return db_p
        return (self.config_dir / db_p).resolve()
