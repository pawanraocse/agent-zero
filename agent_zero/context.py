from dataclasses import dataclass, field, replace
from pathlib import Path

from agent_zero.memory import load_memory, memory_file_scores
from agent_zero.repo_index import (
    index_entries_by_path,
    index_relationships,
    load_repo_index,
)
from agent_zero.tools.file_tools import (
    FileSnippet,
    list_files,
    read_focused_text_file,
    search_text,
)


OVERVIEW_PRIORS = {
    "README.md": 20,
    "docs/high-level-design.md": 18,
    "pyproject.toml": 5,
    "requirements.txt": 4,
}

DEFAULT_CONTEXT_BUDGET_TOKENS = 8_000
DEFAULT_FILE_CONTEXT_CHARS = 6_000
CHARS_PER_TOKEN_ESTIMATE = 4

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
    index_used: bool = False
    memory_used: bool = False
    target_files: list[str] = field(default_factory=list)
    context_budget_tokens: int | None = None
    context_content_tokens: int = 0
    included_files: list[str] = field(default_factory=list)
    truncated_files: list[str] = field(default_factory=list)
    focused_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        return _format_context_decision(self)


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

        sections.extend(
            [
                "Evidence boundary:",
                _format_evidence_boundary(self.decision),
                "Relevance guide:",
                _format_relevance_guide(self.decision),
            ]
        )

        if self.snippets:
            file_sections = []
            for snippet in self.snippets:
                marker = _snippet_marker(snippet)
                file_sections.append(
                    f"### {snippet.path}{marker}\n```text\n{snippet.content}\n```"
                )
            sections.extend(
                ["Included selected file contents:", "\n\n".join(file_sections)]
            )

        return "\n\n".join(sections)


def build_repository_context(
    root: Path,
    task: str,
    max_files: int = 200,
    max_snippets: int = 6,
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> RepositoryContext:
    """Build a small read-only context package for ask mode."""
    root = root.resolve()
    files = list_files(root, max_files=max_files)
    search_results = search_text(root, task)
    repo_index = load_repo_index(root)
    memory_records = load_memory(root)
    decision = decide_context_files(
        files=files,
        task=task,
        search_results=search_results,
        max_snippets=max_snippets,
        repo_index=repo_index,
        memory_records=memory_records,
    )
    snippets, truncated_files, focused_files, skipped_files = _read_budgeted_snippets(
        root=root,
        selected_files=decision.selected_files,
        query_terms=decision.query_terms,
        context_budget_tokens=context_budget_tokens,
    )
    decision = replace(
        decision,
        context_budget_tokens=context_budget_tokens,
        context_content_tokens=_estimate_tokens(
            "\n".join(snippet.content for snippet in snippets)
        ),
        included_files=[snippet.path for snippet in snippets],
        truncated_files=truncated_files,
        focused_files=focused_files,
        skipped_files=skipped_files,
    )

    return RepositoryContext(
        root=root,
        decision=decision,
        files=files,
        snippets=snippets,
        search_results=search_results,
    )


def _read_budgeted_snippets(
    root: Path,
    selected_files: list[str],
    query_terms: list[str],
    context_budget_tokens: int,
) -> tuple[list[FileSnippet], list[str], list[str], list[str]]:
    snippets = []
    truncated_files = []
    focused_files = []
    skipped_files = []
    remaining_chars = max(0, context_budget_tokens * CHARS_PER_TOKEN_ESTIMATE)

    for relative_path in selected_files:
        if remaining_chars <= 0:
            skipped_files.append(relative_path)
            continue

        max_chars = min(DEFAULT_FILE_CONTEXT_CHARS, remaining_chars)
        try:
            snippet = read_focused_text_file(
                root,
                relative_path,
                query_terms=query_terms,
                max_chars=max_chars,
            )
        except (FileNotFoundError, OSError, ValueError):
            continue

        snippets.append(snippet)
        remaining_chars -= len(snippet.content)
        if snippet.truncated:
            truncated_files.append(snippet.path)
        if snippet.focused:
            focused_files.append(snippet.path)

    return snippets, truncated_files, focused_files, skipped_files


def decide_context_files(
    files: list[str],
    task: str,
    search_results: list[str],
    max_snippets: int,
    repo_index: dict | None = None,
    memory_records: list[dict] | None = None,
) -> ContextDecision:
    terms = _query_terms(task)
    search_paths = _paths_from_search_results(search_results)
    index_entries = index_entries_by_path(repo_index)
    memory_scores = memory_file_scores(memory_records or [], terms)
    target_files = _explicit_target_files(task, files)
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    for path in files:
        if not _is_likely_text_context(path):
            continue
        score, path_reasons = _score_file(
            path,
            terms,
            search_paths,
            index_entries.get(path),
        )
        if score > 0:
            scores[path] = score
            reasons[path] = path_reasons

    _apply_memory_boosts(scores, reasons, memory_scores, files)
    _apply_index_relationship_boosts(
        scores,
        reasons,
        index_relationships(repo_index),
        files,
    )
    _apply_target_file_boosts(scores, reasons, target_files)
    _apply_source_type_balance(scores, reasons, terms)

    if not scores or _looks_like_overview_question(task, terms):
        for path, score in OVERVIEW_PRIORS.items():
            if path in files:
                scores[path] = scores.get(path, 0) + score
                reasons.setdefault(path, []).append("overview prior")

    ranked_paths = sorted(scores, key=lambda path: (scores[path], path), reverse=True)
    selected_files = _select_ranked_files(
        ranked_paths,
        terms,
        max_snippets,
        reasons,
        target_files=(
            target_files
            if _looks_like_documentation_target_edit(terms, target_files)
            else []
        ),
    )

    return ContextDecision(
        query_terms=terms,
        selected_files=selected_files,
        reasons={path: reasons[path] for path in selected_files},
        index_used=bool(repo_index),
        memory_used=bool(memory_records),
        target_files=target_files,
    )


def _score_file(
    path: str,
    terms: list[str],
    search_paths: list[str],
    index_entry: dict | None = None,
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

    index_score, index_reasons = _score_index_entry(index_entry, terms)
    if index_score:
        score += index_score
        reasons.extend(index_reasons)

    priority = _file_priority(path)
    if score > 0 and priority:
        score += priority
        reasons.append(f"path priority {priority}")

    return score, reasons


def _score_index_entry(
    index_entry: dict | None,
    terms: list[str],
) -> tuple[int, list[str]]:
    if not index_entry:
        return 0, []

    score = 0
    reasons = []
    concepts = _string_list(index_entry.get("concepts"))
    symbols = _string_list(index_entry.get("symbols"))
    summary = str(index_entry.get("summary", "")).lower()

    concept_matches = [term for term in terms if term in concepts]
    symbol_matches = [
        term
        for term in terms
        if any(term in _normalize(symbol).split() for symbol in symbols)
    ]
    summary_matches = [term for term in terms if term in _normalize(summary).split()]

    if concept_matches:
        score += 6 * len(concept_matches)
        reasons.append(f"index concept matches: {', '.join(concept_matches)}")
    if symbol_matches:
        score += 4 * len(symbol_matches)
        reasons.append(f"index symbol matches: {', '.join(symbol_matches)}")
    if summary_matches:
        score += 3 * len(summary_matches)
        reasons.append(f"index summary matches: {', '.join(summary_matches)}")

    return score, reasons


def _apply_index_relationship_boosts(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    relationships: list[dict[str, str]],
    files: list[str],
) -> None:
    file_set = set(files)
    seeded_paths = {path for path, score in scores.items() if score > 0}
    for relationship in relationships:
        source = relationship["from"]
        target = relationship["to"]
        relationship_type = relationship["type"]
        boost = _relationship_boost(relationship_type)

        if source in seeded_paths and target not in seeded_paths and target in file_set:
            scores[target] = scores.get(target, 0) + boost
            reasons.setdefault(target, []).append(
                f"index related via {relationship_type}: {source} +{boost}"
            )
        if target in seeded_paths and source not in seeded_paths and source in file_set:
            scores[source] = scores.get(source, 0) + boost
            reasons.setdefault(source, []).append(
                f"index related via {relationship_type}: {target} +{boost}"
            )


def _relationship_boost(relationship_type: str) -> int:
    return {
        "imports": 2,
        "tests": 2,
        "mentions": 1,
    }.get(relationship_type, 1)


def _apply_memory_boosts(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    memory_scores: dict[str, int],
    files: list[str],
) -> None:
    file_set = set(files)
    for path, boost in memory_scores.items():
        if path not in file_set or not _is_likely_text_context(path):
            continue
        scores[path] = scores.get(path, 0) + boost
        reasons.setdefault(path, []).append(
            f"memory boost from similar successful task +{boost}"
        )


def _apply_target_file_boosts(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    target_files: list[str],
) -> None:
    for path in target_files:
        scores[path] = scores.get(path, 0) + 40
        reasons.setdefault(path, []).append("explicit target file")


def _apply_source_type_balance(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    terms: list[str],
) -> None:
    if _wants_tests_or_validation(terms):
        return

    for path in list(scores):
        has_direct_reason = _has_direct_retrieval_reason(reasons.get(path, []))
        if path.startswith("agent_zero/") and has_direct_reason:
            scores[path] += 4
            reasons.setdefault(path, []).append("implementation file boost")
        elif path.startswith("tests/"):
            scores[path] -= 6
            reasons.setdefault(path, []).append("test file penalty for non-test task")
            if scores[path] <= 0:
                del scores[path]
                reasons.pop(path, None)


def _wants_tests_or_validation(terms: list[str]) -> bool:
    return bool(
        {"test", "tests", "testing", "pytest", "validation", "validate", "eval"}
        & set(terms)
    )


def _select_ranked_files(
    ranked_paths: list[str],
    terms: list[str],
    max_snippets: int,
    reasons: dict[str, list[str]],
    target_files: list[str] | None = None,
) -> list[str]:
    target_files = target_files or []
    if target_files:
        ranked_target_files = [path for path in ranked_paths if path in target_files]
        if ranked_target_files:
            return ranked_target_files[:max_snippets]

    if _wants_tests_or_validation(terms):
        return ranked_paths[:max_snippets]

    direct_non_tests = [
        path
        for path in ranked_paths
        if not path.startswith("tests/")
        and _has_direct_retrieval_reason(reasons.get(path, []))
    ]
    direct_tests = [
        path
        for path in ranked_paths
        if path.startswith("tests/")
        and _has_direct_retrieval_reason(reasons.get(path, []))
    ]
    related_non_tests = [
        path
        for path in ranked_paths
        if not path.startswith("tests/")
        and not _has_direct_retrieval_reason(reasons.get(path, []))
    ]
    related_tests = [
        path
        for path in ranked_paths
        if path.startswith("tests/")
        and not _has_direct_retrieval_reason(reasons.get(path, []))
    ]

    selected = direct_non_tests[:max_snippets]
    for bucket in (direct_tests, related_non_tests, related_tests):
        if len(selected) >= max_snippets:
            break
        selected.extend(bucket[: max_snippets - len(selected)])

    return selected


def _explicit_target_files(task: str, files: list[str]) -> list[str]:
    lowered = task.lower()
    candidates = []
    aliases = {
        "readme": "README.md",
        "readme.md": "README.md",
        "requirements": "requirements.txt",
        "requirements.txt": "requirements.txt",
        "pyproject": "pyproject.toml",
        "pyproject.toml": "pyproject.toml",
        "hld": "docs/high-level-design.md",
        "high level design": "docs/high-level-design.md",
        "high-level-design": "docs/high-level-design.md",
        "high-level-design.md": "docs/high-level-design.md",
        ".env.example": ".env.example",
        "env example": ".env.example",
    }

    file_set = set(files)
    for alias, path in aliases.items():
        if alias in lowered and path in file_set and path not in candidates:
            candidates.append(path)

    for path in files:
        path_lower = path.lower()
        name_lower = Path(path).name.lower()
        if path_lower in lowered or name_lower in lowered:
            if path not in candidates:
                candidates.append(path)

    return candidates


def _looks_like_documentation_target_edit(
    terms: list[str],
    target_files: list[str],
) -> bool:
    if not target_files:
        return False

    edit_terms = {
        "add",
        "append",
        "change",
        "document",
        "edit",
        "note",
        "remove",
        "update",
        "write",
    }
    if not (edit_terms & set(terms)):
        return False

    return all(
        path == "README.md" or path.startswith("docs/") or path.endswith(".md")
        for path in target_files
    )


def _has_direct_retrieval_reason(reasons: list[str]) -> bool:
    return any(
        reason == "content search hit"
        or reason == "overview prior"
        or reason.startswith("path ")
        or reason.startswith("index concept")
        or reason.startswith("index symbol")
        or reason.startswith("index summary")
        or reason.startswith("memory boost")
        for reason in reasons
    )


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

    lines = [
        f"Query terms: {', '.join(decision.query_terms) or '(none)'}",
        f"Repo index: {'used' if decision.index_used else 'not found'}",
        f"Learning memory: {'used' if decision.memory_used else 'not found'}",
    ]
    if decision.target_files:
        lines.append(f"Target files: {', '.join(decision.target_files)}")
    if decision.context_budget_tokens is not None:
        lines.append(f"Context budget: {decision.context_budget_tokens} tokens")
        lines.append(f"Selected content: ~{decision.context_content_tokens} tokens")
        if decision.included_files:
            lines.append(
                f"Included content files: {', '.join(decision.included_files)}"
            )
        if decision.truncated_files:
            lines.append(f"Truncated files: {', '.join(decision.truncated_files)}")
        if decision.focused_files:
            lines.append(f"Focused files: {', '.join(decision.focused_files)}")
        if decision.skipped_files:
            lines.append(f"Skipped files: {', '.join(decision.skipped_files)}")
    for path in decision.selected_files:
        reason_text = "; ".join(decision.reasons.get(path, [])) or "selected"
        lines.append(f"- {path}: {reason_text}")
    return "\n".join(lines)


def _format_evidence_boundary(decision: ContextDecision) -> str:
    included = ", ".join(decision.included_files) or "(none)"
    skipped = ", ".join(decision.skipped_files) or "(none)"
    return "\n".join(
        [
            f"Included content files: {included}",
            f"Selected but content skipped: {skipped}",
            (
                "Use included file contents for detailed claims. Treat skipped "
                "files as relevance signals only unless their search result lines "
                "show the needed detail."
            ),
        ]
    )


def _format_relevance_guide(decision: ContextDecision) -> str:
    if not decision.selected_files:
        return "(no relevance guide available)"

    lines = []
    for path in decision.selected_files:
        evidence_level = _evidence_level(path, decision)
        reason_text = _human_relevance_reason(decision.reasons.get(path, []))
        lines.append(f"- {path}: {evidence_level}; {reason_text}")

    lines.append(
        "When answering, mention why a file is relevant only if that relevance is "
        "supported by included content, search result lines, or the reasons above."
    )
    return "\n".join(lines)


def _evidence_level(path: str, decision: ContextDecision) -> str:
    if path in decision.included_files:
        if path in decision.focused_files:
            return "primary evidence from focused included content"
        if path in decision.truncated_files:
            return "primary evidence from truncated included content"
        return "primary evidence from included content"
    if path in decision.skipped_files:
        return "relevance signal only because content was skipped"
    return "supporting relevance signal"


def _human_relevance_reason(reasons: list[str]) -> str:
    if not reasons:
        return "selected by repository context ranking"

    readable = []
    for reason in reasons:
        readable.append(_humanize_relevance_reason(reason))
    return "; ".join(readable)


def _humanize_relevance_reason(reason: str) -> str:
    if reason == "content search hit":
        return "matched repository search results"
    if reason == "overview prior":
        return "likely project overview file"
    if reason == "explicit target file":
        return "explicitly named in the task"
    if reason == "implementation file boost":
        return "implementation file for a non-test question"
    if reason == "test file penalty for non-test task":
        return "test file included after implementation evidence"
    if reason.startswith("path priority"):
        return "path is usually useful for this project type"
    if reason.startswith("path token matches:"):
        return "path matches query terms: " + reason.split(":", 1)[1].strip()
    if reason.startswith("path fuzzy matches:"):
        return "path partially matches query terms: " + reason.split(":", 1)[1].strip()
    if reason.startswith("index concept matches:"):
        return "repo index concepts match: " + reason.split(":", 1)[1].strip()
    if reason.startswith("index symbol matches:"):
        return "repo index symbols match: " + reason.split(":", 1)[1].strip()
    if reason.startswith("index summary matches:"):
        return "repo index summary matches: " + reason.split(":", 1)[1].strip()
    if reason.startswith("index related via"):
        return "repo index relationship: " + reason.replace("index related via ", "")
    if reason.startswith("memory boost"):
        return "learning memory says this file helped similar tasks"
    return reason


def _is_likely_text_context(path: str) -> bool:
    return path.endswith(
        (".example", ".md", ".py", ".toml", ".txt", ".yaml", ".yml", ".json")
    )


def _snippet_marker(snippet: FileSnippet) -> str:
    if snippet.focused:
        return " (focused excerpt)"
    if snippet.truncated:
        return " (truncated)"
    return ""


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.lower() for item in value if isinstance(item, str)]


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0

    return max(1, round(len(text) / CHARS_PER_TOKEN_ESTIMATE))
