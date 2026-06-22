import ast
from dataclasses import dataclass
from pathlib import Path


IGNORED_DIRS = {
    ".git",
    ".agent-zero",
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

METHOD_SLICE_TERMS = {
    "content",
    "data",
    "error",
    "get",
    "headers",
    "json",
    "maxtokens",
    "model",
    "payload",
    "poll",
    "post",
    "raise",
    "request",
    "request_id",
    "response",
    "return",
    "status",
    "tenant",
    "tenantid",
    "timeout",
    "topp",
}


@dataclass(frozen=True)
class FileSnippet:
    path: str
    content: str
    truncated: bool
    focused: bool = False


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


def read_focused_text_file(
    root: Path,
    relative_path: str,
    query_terms: list[str],
    max_chars: int = 6000,
    context_lines: int = 3,
) -> FileSnippet:
    """Read a text file, preferring excerpts around query terms when truncated."""
    path = _resolve_repo_path(root, relative_path)
    if _is_ignored(path, root):
        raise ValueError(f"Refusing to read ignored path: {relative_path}")
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError(f"Refusing to read non-text path: {relative_path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) <= max_chars:
        return FileSnippet(
            path=path.relative_to(root).as_posix(),
            content=content,
            truncated=False,
        )

    focused_content = _focused_excerpt(
        content=content,
        path=path,
        query_terms=query_terms,
        max_chars=max_chars,
        context_lines=context_lines,
    )
    return FileSnippet(
        path=path.relative_to(root).as_posix(),
        content=focused_content,
        truncated=True,
        focused=focused_content != content[:max_chars],
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


def _focused_excerpt(
    content: str,
    path: Path,
    query_terms: list[str],
    max_chars: int,
    context_lines: int,
) -> str:
    terms = [term.lower() for term in query_terms if term]
    if not terms:
        return content[:max_chars]

    lines = content.splitlines()
    match_indexes = [
        index
        for index, line in enumerate(lines)
        if any(term in line.lower() for term in terms)
    ]
    if not match_indexes:
        return content[:max_chars]

    if path.suffix.lower() == ".py":
        symbol_excerpt = _symbol_excerpt(content, match_indexes, terms, max_chars)
        if symbol_excerpt:
            return symbol_excerpt

    windows = _merge_line_windows(match_indexes, len(lines), context_lines)
    chunks = []
    for start, end in windows:
        chunk = f"... lines {start + 1}-{end + 1} ...\n"
        chunk += "\n".join(lines[start : end + 1])
        chunks.append(chunk)

    return _fit_chunks(chunks, max_chars) or content[:max_chars]


def _symbol_excerpt(
    content: str,
    match_indexes: list[int],
    terms: list[str],
    max_chars: int,
) -> str:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return ""

    ranges = _matching_symbol_ranges(tree, content.splitlines(), match_indexes, terms)
    if not ranges:
        return ""

    lines = content.splitlines()
    chunks = []
    for node, start, end, name, _score in ranges:
        chunk = _symbol_chunk(node, lines, terms, max_chars)
        if not chunk:
            chunk = f"... symbol {name} lines {start + 1}-{end + 1} ...\n"
            chunk += "\n".join(lines[start : end + 1])
        chunks.append(chunk)

    return _fit_chunks(chunks, max_chars)


def _matching_symbol_ranges(
    tree: ast.AST,
    lines: list[str],
    match_indexes: list[int],
    terms: list[str],
) -> list[
    tuple[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, int, int, str, int]
]:
    symbol_ranges = []
    for node in ast.walk(tree):
        if not isinstance(
            node,
            ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ):
            continue
        if not hasattr(node, "end_lineno"):
            continue
        start = node.lineno - 1
        end = node.end_lineno - 1
        if any(start <= match_index <= end for match_index in match_indexes):
            symbol_ranges.append(
                (
                    node,
                    start,
                    end,
                    _symbol_name(node),
                    _symbol_score(node, lines, terms),
                )
            )

    symbol_ranges.sort(key=lambda item: (-item[4], item[2] - item[1], item[1]))
    selected = []
    seen = set()
    for node, start, end, name, score in symbol_ranges:
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        selected.append((node, start, end, name, score))

    return selected


def _symbol_chunk(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    terms: list[str],
    max_chars: int,
) -> str:
    start = node.lineno - 1
    end = node.end_lineno - 1
    name = _symbol_name(node)
    full_chunk = f"... symbol {name} lines {start + 1}-{end + 1} ...\n"
    full_chunk += "\n".join(lines[start : end + 1])
    if len(full_chunk) <= max_chars or not isinstance(node, ast.ClassDef):
        return full_chunk

    sliced = _class_symbol_chunk(node, lines, terms, max_chars)
    return sliced or full_chunk


def _class_symbol_chunk(
    node: ast.ClassDef,
    lines: list[str],
    terms: list[str],
    max_chars: int,
) -> str:
    methods = [
        child
        for child in node.body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        and hasattr(child, "end_lineno")
    ]
    if not methods:
        return ""

    class_start = node.lineno - 1
    first_method_start = min(method.lineno for method in methods) - 1
    header_end = max(class_start, first_method_start - 1)
    header = f"... symbol class {node.name} lines {node.lineno}-{node.end_lineno} sliced ...\n"
    header += "\n".join(lines[class_start : header_end + 1])

    method_chunks = []
    for method in sorted(
        methods,
        key=lambda item: (
            -_method_slice_score(item, lines, terms),
            item.lineno,
        ),
    ):
        if _method_slice_score(method, lines, terms) <= 0:
            continue
        method_chunks.append(
            _method_chunk(
                method=method,
                lines=lines,
                terms=terms,
                max_chars=max_chars,
            )
        )

    return _fit_chunks([header, *method_chunks], max_chars)


def _method_chunk(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    terms: list[str],
    max_chars: int,
) -> str:
    method_start = method.lineno - 1
    method_end = method.end_lineno - 1
    full_chunk = (
        f"... method {_symbol_name(method)} lines "
        f"{method.lineno}-{method.end_lineno} ...\n"
    )
    full_chunk += "\n".join(lines[method_start : method_end + 1])
    if len(full_chunk) <= max_chars // 2:
        return full_chunk

    sliced = _sliced_method_chunk(method, lines, terms)
    return sliced or full_chunk


def _sliced_method_chunk(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    terms: list[str],
) -> str:
    method_start = method.lineno - 1
    method_end = method.end_lineno - 1
    body_start = _method_body_start(method, method_end)
    important_indexes = _important_method_line_indexes(
        lines=lines,
        start=body_start,
        end=method_end,
        terms=terms,
    )
    if not important_indexes:
        return ""

    chunks = [
        (
            f"... method {_symbol_name(method)} lines "
            f"{method.lineno}-{method.end_lineno} sliced ..."
        ),
        "\n".join(lines[method_start:body_start]),
    ]
    windows = _merge_line_windows(important_indexes, len(lines), context_lines=1)
    for start, end in windows:
        start = max(start, body_start)
        end = min(end, method_end)
        if start <= end:
            chunks.append(f"... lines {start + 1}-{end + 1} ...")
            chunks.append("\n".join(lines[start : end + 1]))

    return "\n".join(part for part in chunks if part)


def _method_body_start(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    method_end: int,
) -> int:
    if not method.body:
        return method.lineno
    return min(getattr(child, "lineno", method.end_lineno) for child in method.body) - 1


def _important_method_line_indexes(
    lines: list[str],
    start: int,
    end: int,
    terms: list[str],
) -> list[int]:
    important_terms = {*terms, *METHOD_SLICE_TERMS}
    indexes = []
    for index in range(start, end + 1):
        normalized_line = _normalize_identifier(lines[index])
        if any(term in normalized_line for term in important_terms):
            indexes.append(index)
    return indexes


def _method_slice_score(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    terms: list[str],
) -> int:
    score = _symbol_score(node, lines, terms)
    method_body = "\n".join(lines[node.lineno - 1 : node.end_lineno]).lower()
    score += min(
        12,
        sum(
            1
            for term in METHOD_SLICE_TERMS
            if term in _normalize_identifier(method_body)
        ),
    )

    if node.name == "complete":
        score += 25
    elif "poll" in node.name:
        score += 20
    elif node.name == "__init__":
        score += 6
    elif not node.name.startswith("_"):
        score += 10
    elif score > 0:
        score += 3
    return score


def _symbol_score(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    terms: list[str],
) -> int:
    start = node.lineno - 1
    end = node.end_lineno - 1
    header = lines[start].lower() if start < len(lines) else ""
    name = _normalize_identifier(node.name)
    body = "\n".join(lines[start : end + 1]).lower()

    score = 0
    for term in terms:
        if term in name or term in header:
            score += 10
        if term in body:
            score += 1
    return score


def _symbol_name(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "class" if isinstance(node, ast.ClassDef) else "def"
    return f"{prefix} {node.name}"


def _normalize_identifier(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else " " for char in value)


def _fit_chunks(chunks: list[str], max_chars: int) -> str:
    excerpt = ""
    separator = "\n\n"
    for chunk in chunks:
        candidate = chunk if not excerpt else f"{excerpt}{separator}{chunk}"
        if len(candidate) <= max_chars:
            excerpt = candidate
            continue

        remaining = max_chars - len(excerpt) - (len(separator) if excerpt else 0)
        if remaining > 0:
            prefix = separator if excerpt else ""
            excerpt = f"{excerpt}{prefix}{chunk[:remaining]}"
        break

    return excerpt


def _merge_line_windows(
    match_indexes: list[int],
    line_count: int,
    context_lines: int,
) -> list[tuple[int, int]]:
    windows = []
    for index in match_indexes:
        start = max(0, index - context_lines)
        end = min(line_count - 1, index + context_lines)
        if windows and start <= windows[-1][1] + 1:
            previous_start, previous_end = windows[-1]
            windows[-1] = (previous_start, max(previous_end, end))
        else:
            windows.append((start, end))
    return windows


def _resolve_repo_path(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    path = (root / relative_path).resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"Path escapes repository: {relative_path}")
    if not path.is_file():
        raise FileNotFoundError(relative_path)
    return path


def _is_ignored(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        try:
            relative_parts = path.resolve().relative_to(root.resolve()).parts
        except ValueError:
            return True
    if any(part in IGNORED_DIRS for part in relative_parts[:-1]):
        return True
    return path.name in IGNORED_FILES or path.name.endswith((".pyc", ".pyo"))
