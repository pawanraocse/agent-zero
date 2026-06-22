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
    records = _compact_memory_records(
        [*records, {"created_at": datetime.now(UTC).isoformat(), **record}]
    )
    records = records[-max_records:]
    path.write_text(
        "\n".join(json.dumps(item, sort_keys=True) for item in records) + "\n",
        encoding="utf-8",
    )
    return path


def _compact_memory_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    by_key: dict[tuple, int] = {}

    for record in records:
        key = _memory_compaction_key(record)
        if key not in by_key:
            record.setdefault("occurrences", 1)
            by_key[key] = len(compacted)
            compacted.append(record)
            continue

        existing = compacted[by_key[key]]
        existing["occurrences"] = int(existing.get("occurrences", 1)) + int(
            record.get("occurrences", 1)
        )
        existing["last_seen_at"] = record.get("created_at", existing.get("created_at"))
        existing["reflection"] = _stronger_reflection(
            existing.get("reflection"),
            record.get("reflection"),
        )
        if record.get("usage") is not None:
            existing["usage"] = record["usage"]

    return compacted


def _memory_compaction_key(record: dict[str, Any]) -> tuple:
    return (
        record.get("mode"),
        tuple(_string_list(record.get("task_terms"))),
        record.get("status"),
        bool(record.get("success", False)),
        tuple(_string_list(record.get("useful_files"))),
        tuple(_string_list(record.get("changed_files"))),
    )


def _stronger_reflection(existing: Any, new: Any) -> Any:
    if not isinstance(existing, dict):
        return new
    if not isinstance(new, dict):
        return existing

    if _confidence_rank(str(new.get("confidence", "low"))) > _confidence_rank(
        str(existing.get("confidence", "low"))
    ):
        return new
    return existing


def _confidence_rank(confidence: str) -> int:
    return {
        "low": 0,
        "medium": 1,
        "high": 2,
    }.get(confidence, 0)


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


def build_reflection(record: dict[str, Any]) -> dict[str, Any]:
    useful_files = _useful_paths(record)
    task_terms_value = _string_list(record.get("task_terms"))
    mode = str(record.get("mode", "unknown"))
    status = str(record.get("status", "unknown"))
    success = bool(record.get("success", False))

    lesson = _reflection_lesson(
        mode=mode,
        status=status,
        success=success,
        useful_files=useful_files,
        validation_passed=record.get("validation_passed"),
    )
    confidence = _reflection_confidence(record, useful_files)

    return {
        "lesson": lesson,
        "task_terms": task_terms_value,
        "useful_files": useful_files,
        "confidence": confidence,
    }


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


def _reflection_lesson(
    mode: str,
    status: str,
    success: bool,
    useful_files: list[str],
    validation_passed: Any,
) -> str:
    if not success:
        return f"{mode} run ended with {status}; do not boost files from this run."

    if mode == "code" and useful_files:
        validation_text = (
            " with validation passing" if validation_passed is True else ""
        )
        return (
            f"{mode} run succeeded{validation_text}; future similar tasks may "
            f"reuse changed files: {', '.join(useful_files)}."
        )

    if useful_files:
        return (
            f"{mode} run succeeded; future similar tasks may inspect useful "
            f"files: {', '.join(useful_files)}."
        )

    return f"{mode} run succeeded, but no reusable file signal was recorded."


def _reflection_confidence(record: dict[str, Any], useful_files: list[str]) -> str:
    if not record.get("success", False):
        return "low"
    if record.get("validation_passed") is True:
        return "high"
    if useful_files:
        return "medium"
    return "low"


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
