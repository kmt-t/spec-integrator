"""Source group resolution and file discovery."""

import fnmatch
from pathlib import Path

from spec_integrator.config import Config


class SourceDiscovery:
    """Selects source files from the configured verification groups."""

    def __init__(self, config: Config):
        self.config = config
        self.root_dir = config.config_dir

    def resolve_group_names(self, group_filter: str | None = None) -> list[str]:
        all_groups = self.config.source_verification.groups
        if not group_filter or group_filter.lower() in ("all", "*"):
            return list(all_groups.keys())

        group = group_filter.lower()
        if group == "python":
            return [name for name in all_groups if name.startswith("python_")]
        if group in ("concepts", "concept"):
            return [name for name in all_groups if "concept" in name]
        if group in ("formal", "model"):
            return [name for name in all_groups if "formal" in name]
        if group in ("pysim", "sim"):
            return [name for name in all_groups if "pysim" in name]
        if group in all_groups:
            return [group]
        return [name for name in all_groups if group in name]

    def collect_files(
        self,
        group_name: str,
        explicit_files: list[str | Path] | None = None,
    ) -> list[Path]:
        group_config = self.config.source_verification.groups.get(group_name)
        if not group_config:
            return []

        def matches(path: Path) -> bool:
            extension = path.suffix.lower()
            extensions_match = not group_config.extensions or extension in [
                item.lower() for item in group_config.extensions
            ]
            patterns_match = not group_config.patterns or any(
                fnmatch.fnmatch(path.name, pattern) for pattern in group_config.patterns
            )
            return extensions_match and patterns_match

        if explicit_files:
            return sorted(
                {
                    path
                    for item in explicit_files
                    for path in [Path(item).resolve()]
                    if path.is_file() and matches(path)
                }
            )

        collected: list[Path] = []
        for include_dir in group_config.include_dirs:
            directory = self.root_dir / include_dir
            if not directory.exists():
                continue
            collected.extend(
                path.resolve()
                for path in directory.rglob("*")
                if path.is_file() and matches(path)
            )
        return sorted(set(collected))


__all__ = ["SourceDiscovery"]
