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


@dataclass
class ProjectConfig:
    name: str = "Spec Project"
    docs_root: str = "docs"
    cache_db: str = ".spec-integrator/doc_cache.db"


@dataclass
class Config:
    version: str = "1.0"
    project: ProjectConfig = field(default_factory=ProjectConfig)
    tiers: list[TierConfig] = field(default_factory=list)
    keywords: dict[str, KeywordRule] = field(default_factory=dict)
    formal_verification: FormalVerificationConfig = field(default_factory=FormalVerificationConfig)
    wit_verification: WITVerificationConfig = field(default_factory=WITVerificationConfig)
    llm_judge: LLMJudgeConfig = field(default_factory=LLMJudgeConfig)
    config_dir: Path = field(default_factory=Path.cwd)

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
        project = ProjectConfig(
            name=proj_data.get("name", "Spec Project"),
            docs_root=proj_data.get("docs_root", "docs"),
            cache_db=proj_data.get("cache_db", ".spec-integrator/doc_cache.db")
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
        formal_verification = FormalVerificationConfig(
            model_dir_name=fv_data.get("model_dir_name", "formal"),
            tag=fv_data.get("tag", "{VERIFY_FORMAL}"),
            timeout_seconds=fv_data.get("timeout_seconds", 30)
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

        return cls(
            version=str(data.get("version", "1.0")),
            project=project,
            tiers=tiers,
            keywords=keywords,
            formal_verification=formal_verification,
            wit_verification=wit_verification,
            llm_judge=llm_judge,
            config_dir=config_dir
        )

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
