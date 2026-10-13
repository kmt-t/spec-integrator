"""Shared command output and filesystem helpers."""

from pathlib import Path


def _log(message: str) -> None:
    print(message, flush=True)


def log_banner(title: str) -> None:
    _log("=" * 80)
    _log(f" {title}")
    _log("=" * 80)


def _rel_path(path: Path | str) -> str:
    path_obj = Path(path).resolve()
    try:
        return str(path_obj.relative_to(Path.cwd()))
    except ValueError:
        return str(path_obj)


def _write_text(path: str, content: str) -> Path:
    output_path = Path(path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
