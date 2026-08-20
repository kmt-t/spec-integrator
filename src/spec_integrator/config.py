from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
import yaml


def glob_to_regex(pattern: str) -> re.Pattern:
    """Converts a glob pattern supporting ** and wildcards to regex."""
    # Normalize slashes
    pat = pattern.replace("\\", "/")
    # Expand braces if any
    i = 0
    res = "^"
    n = len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                # Check for /**/
                if i + 2 < n and pat[i + 2] == "/":
                    res += "(?:.*/)?"
                    i += 3
                    continue
                else:
                    res += ".*"
                    i += 2
                    continue
            else:
                res += "[^/]*"
                i += 1
                continue
        elif c == "?":
            res += "[^/]"
            i += 1
        elif c in r"\.+^$()|[]{}":
            res += "\\" + c
            i += 1
        else:
            res += c
            i += 1
    res += "$"
    return re.compile(res)


@dataclass
class TierConfig:
    tier: int | str  # 0, 1, 2, 3 or "meta"
    name: str
    path_pattern: str
    description: str = ""

    def matches(self, rel_path: str) -> bool:
        """Checks if a relative path matches the tier's pattern."""
        norm_path = rel_path.replace("\\", "/").lstrip("./")
        patterns = self._expand_braces(self.path_pattern)
        for pat in patterns:
            # Also allow pattern without leading docs/ if rel_path is relative to docs_root
            clean_pat = pat.lstrip("./")
            regex = glob_to_regex(clean_pat)
            if regex.match(norm_path):
                return True
            # Try matching with/without leading docs/
            if clean_pat.startswith("docs/") and not norm_path.startswith("docs/"):
                if glob_to_regex(clean_pat[5:]).match(norm_path):
                    return True
            elif not clean_pat.startswith("docs/") and norm_path.startswith("docs/"):
                if glob_to_regex(f"docs/{clean_pat}").match(norm_path):
                    return True
        return False

    @staticmethod
    def _expand_braces(pattern: str) -> list[str]:
        if "{" not in pattern or "}" not in pattern:
            return [pattern]
        prefix, rest = pattern.split("{", 1)
        braces_content, suffix = rest.split("}", 1)
        choices = [c.strip() for c in braces_content.split(",")]
        res = []
        for choice in choices:
            res.extend(TierConfig._expand_braces(f"{prefix}{choice}{suffix}"))
        return res


@dataclass
class KeywordRule:
    pattern: str
    defined_in: str
    description: str = ""

    def is_definition_file(self, rel_path: str) -> bool:
        norm_path = rel_path.replace("\\", "/").lstrip("./")
        patterns = TierConfig._expand_braces(self.defined_in)
        for pat in patterns:
            clean_pat = pat.lstrip("./")
            regex = glob_to_regex(clean_pat)
            if regex.match(norm_path):
                return True
            if clean_pat.startswith("docs/") and not norm_path.startswith("docs/"):
                if glob_to_regex(clean_pat[5:]).match(norm_path):
                    return True
            elif not clean_pat.startswith("docs/") and norm_path.startswith("docs/"):
                if glob_to_regex(f"docs/{clean_pat}").match(norm_path):
                    return True
        return False


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
        """Determines the tier of a given file path based on configured patterns."""
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
