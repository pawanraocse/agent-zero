from dataclasses import dataclass
from pathlib import Path

from agent_zero.tools.file_tools import (
    FileSnippet,
    list_files,
    read_text_file,
    search_text,
)


OVERVIEW_PRIORS = {
    "README.md": 20,
    "docs/high-level-design.md": 18,
    "pyproject.toml": 5,
    "requirements.txt": 4,
}

STOP_WORDS = {
    "about",
    "after",
    "and",
    "are",
    "can",
    "does",
    "for",
    "from",
    "how",
    "into",
    "what",
    "when",
    "where",
    "which",
    "with",
}


@dataclass(frozen=True)
class ContextDecision:
    query_terms: list[str]
    selected_files: list[str]
    reasons: dict[str, list[str]]


@dataclass(frozen=True)
class RepositoryContext:
    root: Path
    decision: ContextDecision
    files: list[str]
    snippets: list[FileSnippet]
    search_results: list[str]

    def to_prompt(self) -> str:
        sections = [
            f"Repository root: {self.root}",
            "Context selection:",
            _format_context_decision(self.decision),
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
    search_results = search_text(root, task)
    decision = decide_context_files(
        files=files,
        task=task,
        search_results=search_results,
        max_snippets=max_snippets,
    )
    snippets = []

    for relative_path in decision.selected_files:
        try:
            snippets.append(read_text_file(root, relative_path))
        except (FileNotFoundError, OSError, ValueError):
            continue

    return RepositoryContext(
        root=root,
        decision=decision,
        files=files,
        snippets=snippets,
        search_results=search_results,
    )


def decide_context_files(
    files: list[str],
    task: str,
    search_results: list[str],
    max_snippets: int,
) -> ContextDecision:
    terms = _query_terms(task)
    search_paths = _paths_from_search_results(search_results)
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    for path in files:
        if not _is_likely_text_context(path):
            continue
        score, path_reasons = _score_file(path, terms, search_paths)
        if score > 0:
            scores[path] = score
            reasons[path] = path_reasons

    if not scores or _looks_like_overview_question(task, terms):
        for path, score in OVERVIEW_PRIORS.items():
            if path in files:
                scores[path] = scores.get(path, 0) + score
                reasons.setdefault(path, []).append("overview prior")

    ranked_paths = sorted(scores, key=lambda path: (scores[path], path), reverse=True)
    selected_files = ranked_paths[:max_snippets]

    return ContextDecision(
        query_terms=terms,
        selected_files=selected_files,
        reasons={path: reasons[path] for path in selected_files},
    )


def _score_file(
    path: str,
    terms: list[str],
    search_paths: list[str],
) -> tuple[int, list[str]]:
    score = 0
    reasons = []
    normalized_path = _normalize(path)
    path_tokens = set(normalized_path.split())

    exact_term_matches = [term for term in terms if term in path_tokens]
    fuzzy_term_matches = [
        term
        for term in terms
        if term not in exact_term_matches and term in normalized_path
    ]

    if exact_term_matches:
        score += 5 * len(exact_term_matches)
        reasons.append(f"path token matches: {', '.join(exact_term_matches)}")

    if fuzzy_term_matches:
        score += 2 * len(fuzzy_term_matches)
        reasons.append(f"path fuzzy matches: {', '.join(fuzzy_term_matches)}")

    if path in search_paths:
        score += 8
        reasons.append("content search hit")

    priority = _file_priority(path)
    if score > 0 and priority:
        score += priority
        reasons.append(f"path priority {priority}")

    return score, reasons


def _file_priority(path: str) -> int:
    if path.startswith("agent_zero/"):
        return 4
    if path.startswith("tests/"):
        return 3
    if path.startswith("docs/"):
        return 2
    if path in {"README.md", "pyproject.toml", "requirements.txt", ".env.example"}:
        return 1
    return 0


def _query_terms(query: str) -> list[str]:
    terms = []
    for term in _normalize(query).split():
        if len(term) > 2 and term not in STOP_WORDS and term not in terms:
            terms.append(term)
    return terms


def _normalize(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else " " for char in value)


def _paths_from_search_results(results: list[str]) -> list[str]:
    paths = []
    for result in results:
        path = result.split(":", 1)[0]
        if path and path not in paths:
            paths.append(path)
    return paths


def _looks_like_overview_question(task: str, terms: list[str]) -> bool:
    lowered = task.lower()
    if any(phrase in lowered for phrase in ("what does", "what is", "overview")):
        return True
    return bool({"project", "purpose"} & set(terms))


def _format_context_decision(decision: ContextDecision) -> str:
    if not decision.selected_files:
        return "(no files selected)"

    lines = [f"Query terms: {', '.join(decision.query_terms) or '(none)'}"]
    for path in decision.selected_files:
        reason_text = "; ".join(decision.reasons.get(path, [])) or "selected"
        lines.append(f"- {path}: {reason_text}")
    return "\n".join(lines)


def _is_likely_text_context(path: str) -> bool:
    return path.endswith(
        (".example", ".md", ".py", ".toml", ".txt", ".yaml", ".yml", ".json")
    )
