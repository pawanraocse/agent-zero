from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agent_zero.tools.file_tools import (
    FileSnippet,
    list_files,
    read_text_file,
    search_text,
)


class ContextIntent(StrEnum):
    OVERVIEW = "overview"
    IMPLEMENTATION = "implementation"
    CONFIG = "config"
    TESTS = "tests"
    DOCS = "docs"


INTENT_CONTEXT_FILES = {
    ContextIntent.OVERVIEW: [
        "README.md",
        "docs/high-level-design.md",
        "pyproject.toml",
        "requirements.txt",
    ],
    ContextIntent.IMPLEMENTATION: [
        "agent_zero/cli.py",
        "agent_zero/context.py",
        "agent_zero/model_client.py",
        "agent_zero/config.py",
    ],
    ContextIntent.CONFIG: [
        "agent_zero/config.py",
        "agent_zero/model_client.py",
        ".env.example",
        "README.md",
    ],
    ContextIntent.TESTS: [
        "tests/test_cli.py",
        "tests/test_context.py",
        "tests/test_file_tools.py",
        "tests/test_model_client.py",
        "tests/test_config.py",
    ],
    ContextIntent.DOCS: [
        "README.md",
        "docs/high-level-design.md",
    ],
}

FALLBACK_CONTEXT_FILES = [
    "README.md",
    "pyproject.toml",
]


@dataclass(frozen=True)
class RepositoryContext:
    root: Path
    intent: ContextIntent
    files: list[str]
    snippets: list[FileSnippet]
    search_results: list[str]

    def to_prompt(self) -> str:
        sections = [
            f"Repository root: {self.root}",
            f"Context intent: {self.intent.value}",
            "Repository files:",
            "\n".join(f"- {path}" for path in self.files) or "(no files found)",
        ]

        if self.search_results:
            sections.extend(
                [
                    "Search results:",
                    "\n".join(f"- {result}" for result in self.search_results),
                ]
            )

        if self.snippets:
            file_sections = []
            for snippet in self.snippets:
                marker = " (truncated)" if snippet.truncated else ""
                file_sections.append(
                    f"### {snippet.path}{marker}\n```text\n{snippet.content}\n```"
                )
            sections.extend(["Selected file contents:", "\n\n".join(file_sections)])

        return "\n\n".join(sections)


def build_repository_context(
    root: Path,
    task: str,
    max_files: int = 200,
    max_snippets: int = 6,
) -> RepositoryContext:
    """Build a small read-only context package for ask mode."""
    files = list_files(root, max_files=max_files)
    intent = classify_context_intent(task)
    search_results = search_text(root, task)
    selected_paths = _select_context_files(
        files=files,
        task=task,
        search_results=search_results,
        intent=intent,
        max_snippets=max_snippets,
    )
    snippets = []

    for relative_path in selected_paths:
        try:
            snippets.append(read_text_file(root, relative_path))
        except (FileNotFoundError, OSError, ValueError):
            continue

    return RepositoryContext(
        root=root,
        intent=intent,
        files=files,
        snippets=snippets,
        search_results=search_results,
    )


def classify_context_intent(task: str) -> ContextIntent:
    lowered = task.lower()

    if _has_any(lowered, {"test", "pytest", "coverage", "assert", "validation"}):
        return ContextIntent.TESTS
    if _has_any(lowered, {"env", "config", "api key", "bedrock", "provider", "token"}):
        return ContextIntent.CONFIG
    if _has_any(lowered, {"readme", "hld", "doc", "documentation", "milestone"}):
        return ContextIntent.DOCS
    if _has_any(
        lowered,
        {
            "bug",
            "class",
            "cli",
            "client",
            "code",
            "error",
            "flow",
            "function",
            "how",
            "implement",
            "where",
        },
    ):
        return ContextIntent.IMPLEMENTATION
    return ContextIntent.OVERVIEW


def _select_context_files(
    files: list[str],
    task: str,
    search_results: list[str],
    intent: ContextIntent,
    max_snippets: int,
) -> list[str]:
    selected: list[str] = []
    for path in INTENT_CONTEXT_FILES[intent]:
        if path in files:
            selected.append(path)

    if not selected:
        for path in FALLBACK_CONTEXT_FILES:
            if path in files:
                selected.append(path)

    for path in _paths_from_search_results(search_results):
        if len(selected) >= max_snippets:
            break
        if path in files and path not in selected and _is_likely_text_context(path):
            selected.append(path)

    terms = _query_terms(task)
    for path in _rank_files(files, terms, selected):
        if len(selected) >= max_snippets:
            break
        selected.append(path)

    return selected[:max_snippets]


def _rank_files(files: list[str], terms: list[str], selected: list[str]) -> list[str]:
    ranked = sorted(
        (
            (_score_file(path, terms), _file_priority(path), path)
            for path in files
            if path not in selected and _is_likely_text_context(path)
        ),
        reverse=True,
    )
    return [path for score, _, path in ranked if score > 0]


def _score_file(path: str, terms: list[str]) -> int:
    lowered = path.lower()
    return sum(1 for term in terms if term in lowered)


def _file_priority(path: str) -> int:
    if path.startswith("agent_zero/"):
        return 4
    if path.startswith("tests/"):
        return 3
    if path.startswith("docs/"):
        return 2
    if path == "README.md":
        return 1
    return 0


def _query_terms(query: str) -> list[str]:
    return [
        term
        for term in "".join(
            char.lower() if char.isalnum() else " " for char in query
        ).split()
        if len(term) > 2
    ]


def _has_any(text: str, candidates: set[str]) -> bool:
    return any(candidate in text for candidate in candidates)


def _paths_from_search_results(results: list[str]) -> list[str]:
    paths = []
    for result in results:
        path = result.split(":", 1)[0]
        if path:
            paths.append(path)
    return paths


def _is_likely_text_context(path: str) -> bool:
    return path.endswith((".md", ".py", ".toml", ".txt", ".yaml", ".yml", ".json"))
