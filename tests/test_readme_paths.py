"""The README's non-example local links must resolve in this repository."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

README = Path(__file__).resolve().parents[1] / "README.md"
EXAMPLE_PATHS = {"FORMAT.md", "report.md"}
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def test_readme_local_paths_exist() -> None:
    repository_root = README.parent
    content = README.read_text(encoding="utf-8")
    missing_paths: list[str] = []
    for raw_target in LINK_PATTERN.findall(content):
        target = raw_target.split("#", 1)[0].split("?", 1)[0].strip("<>")
        if not target or target in EXAMPLE_PATHS:
            continue
        parsed = urlparse(target)
        if parsed.scheme or target.startswith("//"):
            continue
        if not (repository_root / target).exists():
            missing_paths.append(target)
    assert not missing_paths, f"README local paths do not exist: {missing_paths}"
