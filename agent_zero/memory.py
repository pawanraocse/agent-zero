from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


MEMORY_RELATIVE_PATH = Path(".agent-zero/memory.jsonl")
MAX_MEMORY_RECORDS = 100


def load_memory(root: Path) -> list[dict[str, Any]]:
    path = root / MEMORY_RELATIVE_PATH
    if not path.exists():
        return []

    records = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines[-MAX_MEMORY_RECORDS:]:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_memory_record(
    root: Path,
    record: dict[str, Any],
    max_records: int = MAX_MEMORY_RECORDS,
) -> Path:
    path = root / MEMORY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    records = load_memory(root)
    records.append({"created_at": datetime.now(UTC).isoformat(), **record})
    records = records[-max_records:]
    path.write_text(
        "\n".join(json.dumps(item, sort_keys=True) for item in records) + "\n",
        encoding="utf-8",
    )
    return path


def memory_file_scores(
    records: list[dict[str, Any]],
    query_terms: list[str],
) -> dict[str, int]:
    scores: dict[str, int] = {}
    query_set = set(query_terms)
    if not query_set:
        return scores

    for record in records:
        if not record.get("success", False):
            continue
        record_terms = _string_list(record.get("task_terms"))
        overlap = query_set & set(record_terms)
        if not overlap:
            continue

        boost = min(8, 2 * len(overlap))
        for path in _useful_paths(record):
            scores[path] = scores.get(path, 0) + boost

    return scores


def task_terms(task: str) -> list[str]:
    terms = []
    for term in _normalize(task).split():
        if len(term) > 2 and term not in _STOP_TERMS and term not in terms:
            terms.append(term)
    return terms


def _useful_paths(record: dict[str, Any]) -> list[str]:
    paths = []
    for key in ("useful_files", "changed_files"):
        for path in _string_list(record.get(key)):
            if path not in paths:
                paths.append(path)
    return paths


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _normalize(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else " " for char in value)


_STOP_TERMS = {
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
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
}
