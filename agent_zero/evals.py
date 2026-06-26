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
    expected_terms: list[str] | None = None
    forbidden_terms: list[str] | None = None


@dataclass(frozen=True)
class EvalSuite:
    name: str
    evals: list[EvalSpec]


def load_eval_spec(path: Path) -> EvalSpec:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvalSpecError(f"Could not read eval spec: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvalSpecError(f"Eval spec is not valid JSON: {path}") from exc

    return eval_spec_from_dict(data, source=str(path))


def eval_spec_from_dict(data: Any, source: str = "eval spec") -> EvalSpec:
    if not isinstance(data, dict):
        raise EvalSpecError(f"{source} must be a JSON object.")

    name = data.get("name")
    mode = data.get("mode")
    task = data.get("task")
    validation_command = data.get("validation_command")
    expected_terms = data.get("expected_terms")
    forbidden_terms = data.get("forbidden_terms")

    if not isinstance(name, str) or not name.strip():
        raise EvalSpecError(f"{source} requires a non-empty string field: name")
    if mode not in {"ask", "plan", "code"}:
        raise EvalSpecError(f"{source} mode must be one of: ask, plan, code")
    if not isinstance(task, str) or not task.strip():
        raise EvalSpecError(f"{source} requires a non-empty string field: task")
    if validation_command is not None and not isinstance(validation_command, str):
        raise EvalSpecError(f"{source} validation_command must be a string.")
    if expected_terms is not None and not _is_string_list(expected_terms):
        raise EvalSpecError(f"{source} expected_terms must be a list of strings.")
    if forbidden_terms is not None and not _is_string_list(forbidden_terms):
        raise EvalSpecError(f"{source} forbidden_terms must be a list of strings.")

    return EvalSpec(
        name=name.strip(),
        mode=mode,
        task=task.strip(),
        validation_command=validation_command,
        expected_terms=expected_terms,
        forbidden_terms=forbidden_terms,
    )


def load_eval_suite(path: Path) -> EvalSuite:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvalSpecError(f"Could not read eval suite: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvalSpecError(f"Eval suite is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise EvalSpecError("Eval suite must be a JSON object.")

    name = data.get("name")
    eval_items = data.get("evals")
    if not isinstance(name, str) or not name.strip():
        raise EvalSpecError("Eval suite requires a non-empty string field: name")
    if not isinstance(eval_items, list) or not eval_items:
        raise EvalSpecError("Eval suite requires a non-empty list field: evals")

    specs = []
    for index, item in enumerate(eval_items, start=1):
        if isinstance(item, str):
            spec_path = (path.parent / item).resolve()
            specs.append(load_eval_spec(spec_path))
        elif isinstance(item, dict):
            specs.append(eval_spec_from_dict(item, source=f"eval suite item {index}"))
        else:
            raise EvalSpecError(
                "Eval suite evals must contain file paths or eval objects."
            )

    return EvalSuite(name=name.strip(), evals=specs)


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


def write_eval_suite_result(result: dict[str, Any], output_dir: Path) -> Path:
    return write_eval_result(result, output_dir)


def _safe_filename(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in value)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or "eval"


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
