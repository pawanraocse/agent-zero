from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


MEMORY_RELATIVE_PATH = Path(".agent-zero/memory.jsonl")
MEMORY_DB_RELATIVE_PATH = Path(".agent-zero/memory.db")
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


def write_memory_candidate(root: Path, record: dict[str, Any]) -> Path | None:
    candidate = classify_memory_candidate(record)
    if candidate is None:
        return None

    path = root / MEMORY_DB_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    candidate_id = _memory_candidate_id(candidate)
    with sqlite3.connect(path) as connection:
        _ensure_memory_schema(connection)
        existing = connection.execute(
            "select confidence, status, use_count from memory_items where id = ?",
            (candidate_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                insert into memory_items (
                    id, type, scope, claim, status, confidence, evidence_json,
                    task_terms_json, useful_files_json, created_at, updated_at,
                    last_used_at, use_count
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    candidate["type"],
                    candidate["scope"],
                    candidate["claim"],
                    candidate["status"],
                    candidate["confidence"],
                    json.dumps(candidate["evidence"], sort_keys=True),
                    json.dumps(candidate["task_terms"], sort_keys=True),
                    json.dumps(candidate["useful_files"], sort_keys=True),
                    now,
                    now,
                    now,
                    1,
                ),
            )
        else:
            confidence, status, use_count = existing
            connection.execute(
                """
                update memory_items
                set status = ?, confidence = ?, evidence_json = ?,
                    task_terms_json = ?, useful_files_json = ?,
                    updated_at = ?, last_used_at = ?, use_count = ?
                where id = ?
                """,
                (
                    _stronger_status(str(status), str(candidate["status"])),
                    _stronger_confidence(str(confidence), str(candidate["confidence"])),
                    json.dumps(candidate["evidence"], sort_keys=True),
                    json.dumps(candidate["task_terms"], sort_keys=True),
                    json.dumps(candidate["useful_files"], sort_keys=True),
                    now,
                    now,
                    int(use_count) + 1,
                    candidate_id,
                ),
            )

        connection.execute(
            """
            insert into memory_events (
                memory_id, event_type, source, details_json, created_at
            )
            values (?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                "observed",
                str(record.get("mode", "unknown")),
                json.dumps(
                    {
                        "status": record.get("status"),
                        "success": record.get("success"),
                        "reflection": record.get("reflection"),
                    },
                    sort_keys=True,
                ),
                now,
            ),
        )

    return path


def classify_memory_candidate(record: dict[str, Any]) -> dict[str, Any] | None:
    task_terms_value = _string_list(record.get("task_terms"))
    useful_files = _useful_paths(record)
    mode = str(record.get("mode", "unknown"))
    status = str(record.get("status", "unknown"))
    success = bool(record.get("success", False))

    if not success:
        return {
            "type": "failure_lesson",
            "scope": "repo",
            "claim": f"{mode} task with terms {_terms_label(task_terms_value)} ended with {status}.",
            "status": "rejected",
            "confidence": "low",
            "task_terms": task_terms_value,
            "useful_files": useful_files,
            "evidence": [f"status:{status}", "success:false"],
        }

    if useful_files:
        confidence = "high" if record.get("validation_passed") is True else "medium"
        status_value = "confirmed" if confidence == "high" else "candidate"
        return {
            "type": "project_lesson",
            "scope": "repo",
            "claim": (
                f"{mode} task with terms {_terms_label(task_terms_value)} "
                f"used {', '.join(useful_files)}."
            ),
            "status": status_value,
            "confidence": confidence,
            "task_terms": task_terms_value,
            "useful_files": useful_files,
            "evidence": _candidate_evidence(record),
        }

    return {
        "type": "interaction_observation",
        "scope": "repo",
        "claim": (
            f"{mode} task with terms {_terms_label(task_terms_value)} completed "
            "without reusable file evidence."
        ),
        "status": "candidate",
        "confidence": "low",
        "task_terms": task_terms_value,
        "useful_files": [],
        "evidence": [f"status:{status}", "no_reusable_files"],
    }


def load_memory_items(root: Path) -> list[dict[str, Any]]:
    path = root / MEMORY_DB_RELATIVE_PATH
    if not path.exists():
        return []

    with sqlite3.connect(path) as connection:
        _ensure_memory_schema(connection)
        rows = connection.execute(
            """
            select id, type, scope, claim, status, confidence, evidence_json,
                   task_terms_json, useful_files_json, created_at, updated_at,
                   last_used_at, use_count
            from memory_items
            order by updated_at, id
            """
        ).fetchall()

    return [_memory_item_from_row(row) for row in rows]


def memory_item_scores(
    items: list[dict[str, Any]],
    query_terms: list[str],
) -> dict[str, int]:
    scores: dict[str, int] = {}
    query_set = set(query_terms)
    if not query_set:
        return scores

    for item in items:
        if item.get("status") != "confirmed":
            continue
        if item.get("confidence") not in {"medium", "high"}:
            continue

        item_terms = _string_list(item.get("task_terms"))
        overlap = query_set & set(item_terms)
        if not overlap:
            continue

        boost = min(18, 6 * len(overlap))
        for path in _string_list(item.get("useful_files")):
            scores[path] = scores.get(path, 0) + boost

    return scores


def _ensure_memory_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists memory_items (
            id text primary key,
            type text not null,
            scope text not null,
            claim text not null,
            status text not null,
            confidence text not null,
            evidence_json text not null,
            task_terms_json text not null,
            useful_files_json text not null,
            created_at text not null,
            updated_at text not null,
            last_used_at text not null,
            use_count integer not null
        )
        """
    )
    connection.execute(
        """
        create table if not exists memory_events (
            id integer primary key autoincrement,
            memory_id text not null,
            event_type text not null,
            source text not null,
            details_json text not null,
            created_at text not null,
            foreign key(memory_id) references memory_items(id)
        )
        """
    )


def _memory_candidate_id(candidate: dict[str, Any]) -> str:
    raw_key = json.dumps(
        {
            "type": candidate["type"],
            "scope": candidate["scope"],
            "claim": candidate["claim"],
            "useful_files": candidate["useful_files"],
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]


def _candidate_evidence(record: dict[str, Any]) -> list[str]:
    evidence = [f"status:{record.get('status', 'unknown')}"]
    if record.get("validation_passed") is True:
        evidence.append("validation_passed")
    if record.get("changed_files"):
        evidence.append("changed_files")
    if record.get("useful_files"):
        evidence.append("useful_files")
    return evidence


def _terms_label(terms: list[str]) -> str:
    return ", ".join(terms) if terms else "(none)"


def _stronger_confidence(existing: str, new: str) -> str:
    if _confidence_rank(new) > _confidence_rank(existing):
        return new
    return existing


def _stronger_status(existing: str, new: str) -> str:
    if _status_rank(new) > _status_rank(existing):
        return new
    return existing


def _status_rank(status: str) -> int:
    return {
        "rejected": 0,
        "candidate": 1,
        "confirmed": 2,
    }.get(status, 0)


def _memory_item_from_row(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "type": row[1],
        "scope": row[2],
        "claim": row[3],
        "status": row[4],
        "confidence": row[5],
        "evidence": json.loads(row[6]),
        "task_terms": json.loads(row[7]),
        "useful_files": json.loads(row[8]),
        "created_at": row[9],
        "updated_at": row[10],
        "last_used_at": row[11],
        "use_count": row[12],
    }


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
