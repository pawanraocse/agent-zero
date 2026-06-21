from dataclasses import dataclass
from pathlib import Path


IGNORED_DIRS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-debug",
    "__pycache__",
    "agent_zero.egg-info",
    "dist",
    "eval-results",
    "build",
}

IGNORED_FILES = {
    ".env",
}

TEXT_EXTENSIONS = {
    "",
    ".cfg",
    ".example",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class FileSnippet:
    path: str
    content: str
    truncated: bool


def list_files(root: Path, max_files: int = 200) -> list[str]:
    """List repository files that are safe to show to the model."""
    paths: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(paths) >= max_files:
            break
        if not path.is_file() or _is_ignored(path, root):
            continue
        paths.append(path.relative_to(root).as_posix())
    return paths


def read_text_file(
    root: Path, relative_path: str, max_chars: int = 6000
) -> FileSnippet:
    """Read a text file from the repository with simple safety guards."""
    path = _resolve_repo_path(root, relative_path)
    if _is_ignored(path, root):
        raise ValueError(f"Refusing to read ignored path: {relative_path}")
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError(f"Refusing to read non-text path: {relative_path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]

    return FileSnippet(
        path=path.relative_to(root).as_posix(),
        content=content,
        truncated=truncated,
    )


def search_text(
    root: Path,
    query: str,
    max_results: int = 20,
    max_line_chars: int = 240,
) -> list[str]:
    """Search repository text files for simple query terms."""
    terms = _query_terms(query)
    if not terms:
        return []

    results: list[str] = []
    for relative_path in list_files(root):
        if len(results) >= max_results:
            break
        path = root / relative_path
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for line_number, line in enumerate(lines, start=1):
            lowered = line.lower()
            if any(term in lowered for term in terms):
                snippet = line.strip()[:max_line_chars]
                results.append(f"{relative_path}:{line_number}: {snippet}")
                break
    return results


def _query_terms(query: str) -> list[str]:
    ignored = {"a", "an", "and", "for", "in", "is", "me", "of", "on", "the", "to"}
    return [
        term
        for term in "".join(
            char.lower() if char.isalnum() else " " for char in query
        ).split()
        if len(term) > 2 and term not in ignored
    ]


def _resolve_repo_path(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    path = (root / relative_path).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"Path escapes repository: {relative_path}")
    if not path.is_file():
        raise FileNotFoundError(relative_path)
    return path


def _is_ignored(path: Path, root: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    if any(part in IGNORED_DIRS for part in relative_parts[:-1]):
        return True
    return path.name in IGNORED_FILES or path.name.endswith((".pyc", ".pyo"))
