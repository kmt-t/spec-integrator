from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml

T = TypeVar("T")


def normalize_rel_path(path: str | Path) -> str:
    """Normalizes a relative path string with forward slashes and without leading './'."""
    return str(path).replace("\\", "/").lstrip("./")


def regex_match(pattern: str, rel_path: str) -> bool:
    """Matches a relative path against a regular expression pattern."""
    norm_path = normalize_rel_path(rel_path)
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


def _load_dataclass_from_dict(cls: type[T], data: dict[str, Any] | None) -> T:
    """Instantiates a dataclass from a dict, automatically applying defaults for missing fields."""
    if not is_dataclass(cls):
        raise TypeError(f"{cls} must be a dataclass")
    if not isinstance(data, dict):
        return cls()

    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name in data:
            val = data[f.name]
            kwargs[f.name] = val
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Section Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class TierConfig:
    tier: int | str  # 0, 1, 2, 3 or "meta"
    name: str = ""
    path_pattern: str = ""  # Regular expression pattern
    description: str = ""

    def matches(self, rel_path: str) -> bool:
        """Checks if a relative path matches the tier's regex pattern."""
        return regex_match(self.path_pattern, rel_path)


@dataclass
class KeywordRule:
    pattern: str = ""  # Regex for keyword syntax (e.g. ^REQ_[A-Z0-9_]+$)
    defined_in: str = ""  # Regex for definition file path (e.g. requires/.*\.md)
    description: str = ""

    def is_definition_file(self, rel_path: str) -> bool:
        """Checks if the file matches the regex pattern for where the keyword is defined."""
        return regex_match(self.defined_in, rel_path)


@dataclass
class FormalVerificationConfig:
    model_dir_name: str = "formal"
    tag: str = "{VERIFY_FORMAL}"
    timeout_seconds: int = 30
    require_contract: bool = True  # model must expose build_model() + properties()
    check_vacuity: bool = True  # safety property whose violation state is unrepresentable => NG
    check_reachability: bool = True  # states unreachable from S0 => NG
    check_nondeterminism: bool = True  # single-path model cannot exhibit interleaving => NG
    min_states: int = 4  # models smaller than this cannot back a concurrency claim
    check_guard_effectiveness: bool = True


@dataclass
class EvidenceConfig:
    """Evidence Gate: ensures referenced verification artifacts exist."""

    enabled: bool = True
    artifact_extensions: list[str] = field(
        default_factory=lambda: [
            "py",
            "md",
            "wit",
            "tla",
            "json",
            "cfg",
            "yaml",
            "yml",
        ]
    )
    ignore_artifact_refs: list[str] = field(default_factory=list)


@dataclass
class ConsistencyConfig:
    """Consistency Gate: an edit must reach every place that restates the same fact."""

    enabled: bool = True
    symbol_patterns: list[str] = field(
        default_factory=lambda: [
            r"\b[A-Z0-9_]+_(?:CONF|CONFIG|MAX|MIN|SIZE|BASE|LIMIT)\b",
        ]
    )
    extra_scan_globs: list[str] = field(
        default_factory=lambda: [
            "inc/**/*.hxx",
            "inc/*.hxx",
            "src/**/*.hxx",
        ]
    )
    invariants: list[dict] = field(default_factory=list)


@dataclass
class ObligationConfig:
    """Obligation Gate: verification demanded by the risk assessment must not be skipped."""

    enabled: bool = True
    require_assessment: bool = True  # no risk assessment at all => NG
    require_judge: bool = True  # {VERIFY_LLM} tagged but never judged => NG
    risk_threshold: int = 4  # risk_score >= threshold demands recommended verification
    stale_is_error: bool = False  # doc hash difference does not block gate
    require_full_coverage: bool = True  # partial assessment overstates coverage => NG
    forbidden_backends: list[str] = field(default_factory=lambda: ["mock"])


@dataclass
class WITVerificationConfig:
    wit_dir_name: str = "wit"
    tag: str = "{VERIFY_WIT}"


@dataclass
class BenchmarkVerificationConfig:
    benchmark_dir_name: str = "benchmarks"
    tag: str = "{VERIFY_BENCHMARK}"


@dataclass
class LLMBackendConfig:
    api_key_env: str = ""
    endpoint: str = ""
    model: str = ""


@dataclass
class LLMCheckRule:
    """A modular evaluation check rule for LLM document / island review."""

    id: str
    name: str = ""
    mode: list[str] = field(default_factory=lambda: ["single", "cluster"])  # "single", "cluster"
    enabled: bool = True
    severity: str = "ERROR"  # "ERROR" | "WARNING"
    prompt: str = ""
    prompt_file: str = ""
    tags: list[str] = field(default_factory=list)

    def get_prompt_text(self, config_dir: Path) -> str:
        if self.prompt_file:
            path = (config_dir / self.prompt_file).resolve()
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        return self.prompt.strip()


@dataclass
class LLMJudgeConfig:
    tag: str = "{VERIFY_LLM}"
    default_backend: str = "sakura"
    backends: dict[str, LLMBackendConfig] = field(default_factory=dict)
    section_char_budget: int = 8000
    checks: list[LLMCheckRule] = field(default_factory=list)


@dataclass
class ProjectConfig:
    name: str = "Spec Project"
    docs_root: str = "docs"
    cache_db: str = ".spec-integrator/doc_cache.db"
    exclude_patterns: list[str] = field(default_factory=lambda: ["**/FORMAT.md", "FORMAT.md"])


DEFAULT_STOPWORDS = [
    "これ",
    "それ",
    "あれ",
    "どれ",
    "ため",
    "よう",
    "こと",
    "もの",
    "場合",
    "以下",
    "以上",
    "各",
    "等",
    "および",
    "または",
    "また",
    "について",
    "による",
    "における",
    "など",
    "あり",
    "ある",
    "する",
    "され",
    "れる",
    "行い",
    "行う",
    "定義",
    "参照",
    "仕様",
    "設計",
    "機能",
    "概要",
    "項目",
    "詳細",
    "注意",
    "目的",
    "構成",
    "一覧",
    "全体",
    "本ドキュメント",
    "ドキュメント",
    "ファイル",
    "セクション",
    "true",
    "false",
    "none",
    "null",
    "type",
    "name",
    "desc",
    "note",
    "図",
    "表",
    "値",
    "型",
    "例",
    "下記",
    "上記",
    "必須",
    "任意",
    "要求",
    "実装",
    "確認",
    "検証",
    "管理",
    "処理",
    "方式",
    "状態",
]


@dataclass
class TerminologyConfig:
    """Configuration for terminology variance detection."""

    enabled: bool = True
    embedding_model: str = "multilingual-e5-large"
    similarity_threshold: float = 0.90
    confidence_threshold: float = 0.70
    max_terms: int = 500
    stopwords: list[str] = field(default_factory=lambda: list(DEFAULT_STOPWORDS))


@dataclass
class SemanticTopicConfig:
    """Configuration for semantic section topic embedding and duplicate/unlinked detection."""

    enabled: bool = True
    similarity_threshold: float = 0.80
    unlinked_warning_threshold: float = 0.82
    duplicate_warning_threshold: float = 0.90
    embedding_model: str = "multilingual-e5-large"
    backend: str = "sakura"
    batch_size: int = 16
    max_pairs: int = 1000


@dataclass
class TestChainConfig:
    """Configuration for 3-tier design-to-test chain verification."""

    test_dirs: list[str] = field(
        default_factory=lambda: [
            "tests",
            "tests/**",
            "experiments/**",
            "scenarios",
            "scenarios/**",
        ]
    )


@dataclass
class SourceCheckRule:
    """A check rule within a source group."""

    id: str = ""
    enabled: bool = True
    rules: list[str] = field(default_factory=list)


@dataclass
class SourceGroupConfig:
    """Configuration for a specific source group (e.g. cpp, python_concepts)."""

    description: str = ""
    include_dirs: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    formatters: list[str] = field(default_factory=list)
    checks: list[SourceCheckRule] = field(default_factory=list)


@dataclass
class SourceVerificationConfig:
    """Configuration for source code formatting and verification."""

    enabled: bool = True
    groups: dict[str, SourceGroupConfig] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Root Configuration Object
# ---------------------------------------------------------------------------
@dataclass
class Config:
    version: str = "1.0"
    project: ProjectConfig = field(default_factory=ProjectConfig)
    tiers: list[TierConfig] = field(default_factory=list)
    keywords: dict[str, KeywordRule] = field(default_factory=dict)
    formal_verification: FormalVerificationConfig = field(default_factory=FormalVerificationConfig)
    wit_verification: WITVerificationConfig = field(default_factory=WITVerificationConfig)
    benchmark_verification: BenchmarkVerificationConfig = field(
        default_factory=BenchmarkVerificationConfig
    )
    llm_judge: LLMJudgeConfig = field(default_factory=LLMJudgeConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    obligation: ObligationConfig = field(default_factory=ObligationConfig)
    consistency: ConsistencyConfig = field(default_factory=ConsistencyConfig)
    test_chain: TestChainConfig = field(default_factory=TestChainConfig)
    terminology: TerminologyConfig = field(default_factory=TerminologyConfig)
    semantic_topic: SemanticTopicConfig = field(default_factory=SemanticTopicConfig)
    source_verification: SourceVerificationConfig = field(default_factory=SourceVerificationConfig)
    config_dir: Path = field(default_factory=Path.cwd)

    def is_excluded(self, file_path: str | Path, docs_root: Path | None = None) -> bool:
        """Checks if a file path matches any exclusion patterns."""
        import fnmatch

        p_str = normalize_rel_path(file_path)
        if docs_root:
            try:
                p_rel = Path(file_path).relative_to(docs_root).as_posix().lstrip("./")
            except ValueError:
                p_rel = p_str
        else:
            p_rel = p_str

        file_name = Path(p_str).name
        for pat in self.project.exclude_patterns:
            norm_pat = normalize_rel_path(pat)
            if file_name == norm_pat or norm_pat == f"**/{file_name}":
                return True
            if (
                fnmatch.fnmatch(p_rel, norm_pat)
                or fnmatch.fnmatch(p_str, norm_pat)
                or fnmatch.fnmatch(file_name, norm_pat)
            ):
                return True
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

        # 1. Project
        proj_data = data.get("project", {})
        default_excludes = ["**/FORMAT.md", "FORMAT.md"]
        custom_excludes = proj_data.get("exclude_patterns", [])
        combined_excludes = list(dict.fromkeys(default_excludes + custom_excludes))
        project = _load_dataclass_from_dict(ProjectConfig, proj_data)
        project.exclude_patterns = combined_excludes

        # 2. Tiers
        tiers: list[TierConfig] = []
        for t in data.get("tiers", []):
            tier_val = t.get("tier")
            if isinstance(tier_val, str) and tier_val.isdigit():
                tier_val = int(tier_val)
            tiers.append(
                TierConfig(
                    tier=tier_val,
                    name=t.get("name", ""),
                    path_pattern=t.get("path_pattern", ""),
                    description=t.get("description", ""),
                )
            )

        # 3. Keywords
        keywords: dict[str, KeywordRule] = {}
        for k_type, k_data in data.get("keywords", {}).items():
            keywords[k_type] = _load_dataclass_from_dict(KeywordRule, k_data)

        # 4. LLM Judge Backends & Modular Checks
        llm_data = data.get("llm_judge", {})
        backends: dict[str, LLMBackendConfig] = {}
        for b_name, b_info in llm_data.get("backends", {}).items():
            backends[b_name] = _load_dataclass_from_dict(LLMBackendConfig, b_info)

        raw_checks = llm_data.get("checks", [])
        parsed_checks: list[LLMCheckRule] = []
        for c in raw_checks:
            if isinstance(c, dict):
                rule = _load_dataclass_from_dict(LLMCheckRule, c)
                if isinstance(c.get("mode"), str):
                    rule.mode = [c["mode"]]
                parsed_checks.append(rule)

        llm_judge = _load_dataclass_from_dict(LLMJudgeConfig, llm_data)
        if backends:
            llm_judge.backends = backends
        if parsed_checks:
            llm_judge.checks = parsed_checks

        # 5. Invariants file for Consistency Gate
        cs_data = data.get("consistency", {})
        consistency = _load_dataclass_from_dict(ConsistencyConfig, cs_data)
        inv_file = cs_data.get("invariants_file")
        if inv_file:
            inv_path = (
                (config_dir / inv_file) if not Path(inv_file).is_absolute() else Path(inv_file)
            )
            if inv_path.exists():
                with open(inv_path, "r", encoding="utf-8") as f:
                    extra = yaml.safe_load(f) or {}
        # 6. Source verification
        source_verif_raw = data.get("source_verification") or {}
        groups_raw = source_verif_raw.get("groups", {})
        parsed_groups: dict[str, SourceGroupConfig] = {}
        for g_name, g_val in groups_raw.items():
            if isinstance(g_val, dict):
                checks_raw = g_val.get("checks", [])
                parsed_checks = []
                for c in checks_raw:
                    if isinstance(c, dict):
                        parsed_checks.append(_load_dataclass_from_dict(SourceCheckRule, c))
                    elif isinstance(c, str):
                        parsed_checks.append(SourceCheckRule(id=c, enabled=True))
                g_val_copy = dict(g_val)
                g_val_copy["checks"] = parsed_checks
                parsed_groups[g_name] = _load_dataclass_from_dict(SourceGroupConfig, g_val_copy)
        source_verification = SourceVerificationConfig(
            enabled=source_verif_raw.get("enabled", True),
            groups=parsed_groups,
        )

        return cls(
            version=str(data.get("version", "1.0")),
            project=project,
            tiers=tiers,
            keywords=keywords,
            formal_verification=_load_dataclass_from_dict(
                FormalVerificationConfig, data.get("formal_verification")
            ),
            wit_verification=_load_dataclass_from_dict(
                WITVerificationConfig, data.get("wit_verification")
            ),
            benchmark_verification=_load_dataclass_from_dict(
                BenchmarkVerificationConfig, data.get("benchmark_verification")
            ),
            llm_judge=llm_judge,
            evidence=_load_dataclass_from_dict(EvidenceConfig, data.get("evidence")),
            obligation=_load_dataclass_from_dict(ObligationConfig, data.get("obligation")),
            consistency=consistency,
            test_chain=_load_dataclass_from_dict(TestChainConfig, data.get("test_chain")),
            terminology=_load_dataclass_from_dict(TerminologyConfig, data.get("terminology")),
            semantic_topic=_load_dataclass_from_dict(
                SemanticTopicConfig, data.get("semantic_topic")
            ),
            source_verification=source_verification,
            config_dir=config_dir,
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
        for _k_type, rule in self.keywords.items():
            if re.match(rule.pattern, keyword):
                if rule.is_definition_file(file_path):
                    return True
        return False

    def get_docs_dir(self) -> Path:
        return (self.config_dir / self.project.docs_root).resolve()

    def get_db_path(self) -> Path:
        override = os.environ.get("SPEC_INTEGRATOR_DB_PATH")
        if override:
            return Path(override).resolve()
        db_p = Path(self.project.cache_db)
        if db_p.is_absolute():
            return db_p
        return (self.config_dir / db_p).resolve()


__all__ = [
    "BenchmarkVerificationConfig",
    "Config",
    "ConsistencyConfig",
    "EvidenceConfig",
    "FormalVerificationConfig",
    "KeywordRule",
    "LLMBackendConfig",
    "LLMCheckRule",
    "LLMJudgeConfig",
    "ObligationConfig",
    "ProjectConfig",
    "SourceCheckRule",
    "SourceGroupConfig",
    "SourceVerificationConfig",
    "TestChainConfig",
    "TierConfig",
    "WITVerificationConfig",
    "normalize_rel_path",
    "regex_match",
]
