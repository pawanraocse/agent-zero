from __future__ import annotations

import ast
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

from agent_zero.tools.file_tools import list_files, read_text_file


INDEX_RELATIVE_PATH = Path(".agent-zero/index.json")
MAX_CONCEPTS = 18
MAX_SYMBOLS = 20
MAX_IMPORTS = 20
MAX_MENTION_EDGES = 40


def build_repo_index(root: Path) -> dict[str, Any]:
    files = list_files(root, max_files=500)
    file_entries = []
    relationships = []
    path_set = set(files)

    for relative_path in files:
        try:
            content = read_text_file(root, relative_path, max_chars=12000).content
        except (FileNotFoundError, OSError, ValueError):
            continue

        entry = _build_file_entry(relative_path, content, path_set)
        file_entries.append(entry)
        relationships.extend(_import_relationships(relative_path, entry, path_set))
        relationships.extend(_test_relationships(relative_path, path_set))

    relationships.extend(_mention_relationships(file_entries, path_set))

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "root": str(root.resolve()),
        "files": file_entries,
        "relationships": _unique_relationships(relationships),
    }


def write_repo_index(root: Path, output_path: Path | None = None) -> Path:
    index = build_repo_index(root)
    path = output_path or root / INDEX_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def load_repo_index(root: Path) -> dict[str, Any] | None:
    path = root / INDEX_RELATIVE_PATH
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None
    return data


def index_file_count(index: dict[str, Any]) -> int:
    files = index.get("files")
    if isinstance(files, list):
        return len(files)
    return 0


def index_relationship_count(index: dict[str, Any]) -> int:
    relationships = index.get("relationships")
    if isinstance(relationships, list):
        return len(relationships)
    return 0


def index_entries_by_path(index: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not index:
        return {}

    entries = {}
    files = index.get("files", [])
    if not isinstance(files, list):
        return entries

    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if isinstance(path, str):
            entries[path] = entry
    return entries


def index_relationships(index: dict[str, Any] | None) -> list[dict[str, str]]:
    if not index:
        return []

    relationships = index.get("relationships", [])
    if not isinstance(relationships, list):
        return []

    valid_relationships = []
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        source = relationship.get("from")
        target = relationship.get("to")
        relationship_type = relationship.get("type")
        if (
            isinstance(source, str)
            and isinstance(target, str)
            and isinstance(relationship_type, str)
        ):
            valid_relationships.append(
                {"from": source, "to": target, "type": relationship_type}
            )
    return valid_relationships


def _build_file_entry(
    relative_path: str,
    content: str,
    path_set: set[str],
) -> dict[str, Any]:
    file_type = _file_type(relative_path)
    symbols = _symbols_for_file(relative_path, content)
    imports = _imports_for_file(relative_path, content, path_set)
    concepts = _concepts_for_file(relative_path, content, symbols, imports)
    summary = _summary_for_file(relative_path, file_type, content, symbols, imports)

    return {
        "path": relative_path,
        "type": file_type,
        "summary": summary,
        "concepts": concepts,
        "symbols": symbols,
        "imports": imports,
    }


def _file_type(path: str) -> str:
    if path.startswith("tests/"):
        return "test"
    if path.startswith("docs/") or path == "README.md":
        return "documentation"
    if path.startswith("evals/"):
        return "eval"
    if path.endswith(".py"):
        return "python"
    if path.endswith((".toml", ".txt", ".example")):
        return "configuration"
    if path.endswith(".json"):
        return "data"
    return "text"


def _symbols_for_file(path: str, content: str) -> list[str]:
    if not path.endswith(".py"):
        return _markdown_headings(content)[:MAX_SYMBOLS]

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    symbols = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)

    return _unique(symbols)[:MAX_SYMBOLS]


def _imports_for_file(path: str, content: str, path_set: set[str]) -> list[str]:
    if not path.endswith(".py"):
        return []

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    local_imports = []
    for module in imports:
        local_path = _module_to_path(module)
        if local_path in path_set:
            local_imports.append(local_path)
    return _unique(local_imports)[:MAX_IMPORTS]


def _concepts_for_file(
    path: str,
    content: str,
    symbols: list[str],
    imports: list[str],
) -> list[str]:
    candidates = []
    candidates.extend(_tokens(path))
    candidates.extend(_tokens(" ".join(symbols)))
    candidates.extend(_tokens(" ".join(imports)))
    candidates.extend(_tokens(" ".join(_markdown_headings(content))))
    candidates.extend(_tokens(" ".join(_top_identifier_terms(content))))
    return _unique(candidates)[:MAX_CONCEPTS]


def _summary_for_file(
    path: str,
    file_type: str,
    content: str,
    symbols: list[str],
    imports: list[str],
) -> str:
    if file_type == "documentation":
        headings = _markdown_headings(content)
        if headings:
            return f"Documentation covering: {', '.join(headings[:4])}."
        return "Documentation file for project explanation and usage."

    if file_type == "test":
        target = _test_target(path)
        target_text = f" for {target}" if target else ""
        return f"Test module{target_text}. Defines: {_short_list(symbols)}."

    if file_type == "python":
        imports_text = (
            f" Imports local modules: {_short_list(imports)}." if imports else ""
        )
        return f"Python module defining: {_short_list(symbols)}.{imports_text}"

    if file_type == "eval":
        return "Eval specification used to run a repeatable Agent Zero task."

    if file_type == "configuration":
        return "Configuration or dependency file for project setup."

    return "Text or data file included in the repository."


def _import_relationships(
    path: str,
    entry: dict[str, Any],
    path_set: set[str],
) -> list[dict[str, str]]:
    relationships = []
    imports = entry.get("imports", [])
    if not isinstance(imports, list):
        return relationships

    for imported_path in imports:
        if isinstance(imported_path, str) and imported_path in path_set:
            relationships.append({"from": path, "to": imported_path, "type": "imports"})
    return relationships


def _test_relationships(path: str, path_set: set[str]) -> list[dict[str, str]]:
    target = _test_target(path)
    if target and target in path_set:
        return [{"from": path, "to": target, "type": "tests"}]
    return []


def _mention_relationships(
    entries: list[dict[str, Any]],
    path_set: set[str],
) -> list[dict[str, str]]:
    relationships = []
    paths = sorted(path_set)

    for entry in entries:
        source = entry["path"]
        if len(relationships) >= MAX_MENTION_EDGES:
            break
        summary = entry.get("summary", "")
        concepts = " ".join(entry.get("concepts", []))
        haystack = f"{summary} {concepts}"
        for target in paths:
            if target == source:
                continue
            if target in haystack:
                relationships.append({"from": source, "to": target, "type": "mentions"})
                if len(relationships) >= MAX_MENTION_EDGES:
                    break

    return relationships


def _test_target(path: str) -> str | None:
    if not path.startswith("tests/test_") or not path.endswith(".py"):
        return None

    module_name = Path(path).stem.removeprefix("test_")
    target = f"agent_zero/{module_name}.py"
    return target


def _module_to_path(module: str) -> str:
    return f"{module.replace('.', '/')}.py"


def _markdown_headings(content: str) -> list[str]:
    headings = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                headings.append(heading)
    return headings


def _top_identifier_terms(content: str) -> list[str]:
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", content)
    counts: dict[str, int] = {}
    for identifier in identifiers:
        lowered = identifier.lower()
        if lowered in _STOP_TERMS:
            continue
        counts[lowered] = counts.get(lowered, 0) + 1
    return sorted(counts, key=lambda term: (counts[term], term), reverse=True)[:20]


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^A-Za-z0-9]+", value.lower())
        if len(token) > 2 and token not in _STOP_TERMS
    ]


def _unique(values: list[str]) -> list[str]:
    unique_values = []
    for value in values:
        if value and value not in unique_values:
            unique_values.append(value)
    return unique_values


def _unique_relationships(
    relationships: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen = set()
    unique_relationships = []
    for relationship in relationships:
        key = (relationship["from"], relationship["to"], relationship["type"])
        if key in seen:
            continue
        seen.add(key)
        unique_relationships.append(relationship)
    return unique_relationships


def _short_list(values: list[str]) -> str:
    if not values:
        return "none"
    return ", ".join(values[:6])


_STOP_TERMS = {
    "and",
    "any",
    "are",
    "but",
    "class",
    "def",
    "for",
    "from",
    "has",
    "import",
    "into",
    "not",
    "none",
    "return",
    "self",
    "str",
    "the",
    "this",
    "true",
    "with",
}
