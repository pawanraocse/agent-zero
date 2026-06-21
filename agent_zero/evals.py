from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal


EvalMode = Literal["ask", "plan", "code"]


class EvalSpecError(RuntimeError):
    """Raised when an eval spec cannot be loaded."""


@dataclass(frozen=True)
class EvalSpec:
    name: str
    mode: EvalMode
    task: str
    validation_command: str | None = None


def load_eval_spec(path: Path) -> EvalSpec:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvalSpecError(f"Could not read eval spec: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvalSpecError(f"Eval spec is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise EvalSpecError("Eval spec must be a JSON object.")

    name = data.get("name")
    mode = data.get("mode")
    task = data.get("task")
    validation_command = data.get("validation_command")

    if not isinstance(name, str) or not name.strip():
        raise EvalSpecError("Eval spec requires a non-empty string field: name")
    if mode not in {"ask", "plan", "code"}:
        raise EvalSpecError("Eval spec mode must be one of: ask, plan, code")
    if not isinstance(task, str) or not task.strip():
        raise EvalSpecError("Eval spec requires a non-empty string field: task")
    if validation_command is not None and not isinstance(validation_command, str):
        raise EvalSpecError("Eval spec validation_command must be a string.")

    return EvalSpec(
        name=name.strip(),
        mode=mode,
        task=task.strip(),
        validation_command=validation_command,
    )


def write_eval_result(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = _safe_filename(str(result.get("name", "eval")))
    path = output_dir / f"{timestamp}-{name}.json"
    path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _safe_filename(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in value)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or "eval"
