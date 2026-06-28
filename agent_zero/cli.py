from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Literal

import typer

from agent_zero.config import ConfigError, load_config
from agent_zero.context import DEFAULT_CONTEXT_BUDGET_TOKENS, build_repository_context
from agent_zero.diff_parser import (
    DiffExtractionError,
    extract_unified_diff,
    is_no_change_response,
)
from agent_zero.diff_summary import (
    diff_summary_has_changes,
    diff_summary_to_dicts,
    format_diff_summary,
    summarize_unified_diff,
)
from agent_zero.evals import (
    EvalSpec,
    EvalSpecError,
    load_eval_spec,
    load_eval_suite,
    write_eval_result,
    write_eval_suite_result,
)
from agent_zero.memory import (
    append_memory_record,
    apply_memory_feedback,
    build_reflection,
    delete_memory_items,
    detect_user_feedback,
    load_memory,
    load_memory_items,
    reset_memory,
    task_terms,
    update_memory_item_status,
    write_memory_candidate,
)
from agent_zero.model_client import ModelClientError, ModelResponse, create_model_client
from agent_zero.repo_index import (
    build_repo_index,
    index_file_count,
    index_relationship_count,
    write_repo_index,
)
from agent_zero.task_classifier import classify_task
from agent_zero.tools.command_tool import CommandRunError, CommandResult, run_command
from agent_zero.tools.patch_tool import PatchApplyError, apply_unified_diff
from agent_zero.usage import (
    TokenUsage,
    estimate_usage_cost,
    format_usage_cost,
    resolve_token_usage,
)

app = typer.Typer(
    help="Agent Zero: a minimal coding agent built from scratch.",
    no_args_is_help=True,
)

TraceLevel = Literal["none", "basic", "debug"]

ASK_SYSTEM_PROMPT = """You are Agent Zero, a minimal coding-agent learning project.
Answer questions using the provided repository context.
Be clear about what the context does and does not show.
Do not claim you edited files or ran validation.
When useful, mention relevant file paths from the context.
Use the relevance guide to explain why files matter.
Distinguish included file contents from selected files whose contents were skipped.
Use skipped files as relevance signals only, unless search result lines show the needed detail."""

PLAN_SYSTEM_PROMPT = """You are Agent Zero in plan mode.
Inspect the provided repository context and produce a structured implementation plan.
Do not edit files. Do not claim you ran validation.
Prefer current code evidence over future-looking documentation when they conflict.
Use the relevance guide to explain why files matter.
Distinguish included file contents from selected files whose contents were skipped.
Use skipped files as relevance signals only, unless search result lines show the needed detail.

Return the plan with these sections:
1. Summary
2. Relevant Files
3. Implementation Steps
4. Validation Steps
5. Risks And Unknowns
6. Confidence Score"""

CODE_SYSTEM_PROMPT = """You are Agent Zero in code mode.
Use the provided repository context to make one focused change.
Return a unified diff only. Do not wrap the diff in prose unless needed.
Do not modify ignored files, secrets, virtual environments, caches, or binary files.
Prefer small patches. If the request is unsafe or impossible from the context, explain why without a diff."""

FIX_VALIDATION_SYSTEM_PROMPT = """You are Agent Zero fixing a failed validation.
Use the original change request, changed files, and validation output.
Return one corrective unified diff only.
Keep the fix as small as possible.
If there is no safe correction, explain why without a diff."""

FIX_EMPTY_PATCH_SYSTEM_PROMPT = """You are Agent Zero fixing an empty patch.
The previous response contained a unified diff, but it had zero additions and
zero deletions, so it would not change the repository.
Return one corrected unified diff only.
If no repository change is actually needed, explain why without a diff."""

FIX_PATCH_APPLICATION_SYSTEM_PROMPT = """You are Agent Zero fixing a patch that
failed to apply. The previous response contained a non-empty unified diff, but
the local patch engine rejected it because the file context did not match.
Return one corrected unified diff only.
Use the current file excerpts as the source of truth."""


@dataclass(frozen=True)
class ValidationStepResult:
    label: str
    result: CommandResult


@dataclass(frozen=True)
class ValidationResult:
    steps: list[ValidationStepResult]

    @property
    def passed(self) -> bool:
        return all(step.result.passed for step in self.steps)

    @property
    def final_step(self) -> ValidationStepResult:
        for step in self.steps:
            if not step.result.passed:
                return step
        return self.steps[-1]

    @property
    def command(self) -> list[str]:
        return self.final_step.result.command

    @property
    def exit_code(self) -> int:
        return self.final_step.result.exit_code

    @property
    def timed_out(self) -> bool:
        return self.final_step.result.timed_out

    @property
    def stdout(self) -> str:
        return self.final_step.result.stdout

    @property
    def stderr(self) -> str:
        return self.final_step.result.stderr


def _load_config_or_exit(env_file: Path | None):
    try:
        return load_config(env_file)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _run_stub(mode: str, task: str, env_file: Path | None) -> None:
    config = _load_config_or_exit(env_file)

    typer.echo(f"Mode: {mode}")
    typer.echo(f"Task: {task}")
    typer.echo(f"Provider: {config.provider}")
    typer.echo(f"Model: {config.model}")
    if config.provider == "openai":
        typer.echo(f"Base URL: {config.base_url}")
    if config.provider == "bedrock":
        typer.echo(f"Bedrock URL: {config.bedrock_url}")
    typer.echo(f"Status: {mode} mode arrives in a later milestone.")


def _build_user_prompt(
    task_label: str,
    task: str,
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> str:
    repository_context = build_repository_context(
        Path.cwd(),
        task,
        context_budget_tokens=context_budget_tokens,
    )
    return _prompt_from_context(task_label, task, repository_context)


def _build_user_prompt_with_context(
    task_label: str,
    task: str,
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
):
    repository_context = build_repository_context(
        Path.cwd(),
        task,
        context_budget_tokens=context_budget_tokens,
    )
    return _prompt_from_context(
        task_label, task, repository_context
    ), repository_context


def _prompt_from_context(task_label: str, task: str, repository_context) -> str:
    return (
        f"{task_label}:\n{task}\n\n"
        f"Repository context:\n{repository_context.to_prompt()}"
    )


def _print_context_selection(repository_context) -> None:
    typer.echo("Context selection:")
    typer.echo(repository_context.decision.to_text())
    typer.echo("")


def _resolve_trace_level(trace: bool, trace_level: str) -> TraceLevel:
    if trace_level not in {"none", "basic", "debug"}:
        typer.secho(
            "Invalid trace level. Use one of: none, basic, debug.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if trace_level == "none" and trace:
        return "basic"
    return trace_level  # type: ignore[return-value]


def _trace_enabled(trace_level: TraceLevel) -> bool:
    return trace_level != "none"


def _debug_trace_enabled(trace_level: TraceLevel) -> bool:
    return trace_level == "debug"


def _print_trace(
    mode: str, repository_context, config, trace_level: TraceLevel
) -> None:
    decision = repository_context.decision
    selected_files = ", ".join(decision.selected_files) or "(none)"
    included_files = ", ".join(decision.included_files) or "(none)"
    truncated_files = ", ".join(decision.truncated_files) or "(none)"
    focused_files = ", ".join(decision.focused_files) or "(none)"
    skipped_files = ", ".join(decision.skipped_files) or "(none)"
    steps = [
        f"Loaded config: provider={config.provider}, model={config.model}",
        f"Listed repository files: {len(repository_context.files)}",
        f"Searched repository text: {len(repository_context.search_results)} result(s)",
        f"Loaded repo index: {'used' if decision.index_used else 'not found'}",
        f"Loaded learning memory: {'used' if decision.memory_used else 'not found'}",
        (
            "Loaded SQLite memory: "
            f"{'used' if decision.sqlite_memory_used else 'not found'}"
        ),
        f"Selected files: {selected_files}",
        f"Included content files: {included_files}",
        (
            "Applied context budget: "
            f"{decision.context_budget_tokens} tokens, "
            f"selected content ~{decision.context_content_tokens} tokens"
        ),
        f"Truncated files: {truncated_files}",
        f"Focused excerpts: {focused_files}",
        f"Skipped file contents: {skipped_files}",
        f"Prepared {mode} prompt and called model",
    ]

    typer.echo("Agent trace:")
    for index, step in enumerate(steps, start=1):
        typer.echo(f"{index}. {step}")
    typer.echo("")

    if _debug_trace_enabled(trace_level):
        _print_debug_trace(repository_context)


def _print_debug_trace(repository_context) -> None:
    decision = repository_context.decision
    typer.echo("Agent trace debug:")
    typer.echo(f"- Query terms: {', '.join(decision.query_terms) or '(none)'}")
    typer.echo(f"- Target files: {', '.join(decision.target_files) or '(none)'}")
    typer.echo("- Selected file reasons:")
    for path in decision.selected_files:
        reason_text = "; ".join(decision.reasons.get(path, [])) or "selected"
        typer.echo(f"  - {path}: {reason_text}")
    typer.echo("- Included content sizes:")
    for snippet in repository_context.snippets:
        typer.echo(f"  - {snippet.path}: {len(snippet.content)} chars")
    typer.echo("")


def _print_trace_json(trace: dict) -> None:
    typer.echo("")
    typer.echo("Trace JSON:")
    typer.echo(json.dumps(trace, indent=2, sort_keys=True))


def _tool_call_record(
    name: str,
    status: str,
    input_summary: str = "",
    output_summary: str = "",
    duration_ms: float | None = None,
    error: str | None = None,
) -> dict:
    record = {
        "name": name,
        "status": status,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "duration_ms": duration_ms if duration_ms is not None else 0.0,
    }
    if error is not None:
        record["error"] = error
    return record


def _timer_start() -> float:
    return time.perf_counter()


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)


def _cost_budget_check(
    system_prompt: str,
    user_prompt: str,
    config,
    max_cost: float | None,
    spent_cost: float = 0.0,
) -> dict | None:
    if max_cost is None:
        return None

    usage = resolve_token_usage(
        response=ModelResponse(content=""),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=config.model,
    )
    input_usage = TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=0,
        total_tokens=usage.input_tokens,
        estimated=True,
    )
    input_cost = None
    if config.input_cost_per_1m_tokens is not None:
        input_cost = estimate_usage_cost(
            input_usage,
            config.model_copy(update={"output_cost_per_1m_tokens": 0.0}),
        )

    projected_cost = None if input_cost is None else spent_cost + input_cost.total_cost
    return {
        "max_cost": _format_cost_value(max_cost),
        "spent_cost": _format_cost_value(spent_cost),
        "estimated_input_tokens": usage.input_tokens,
        "estimated_input_cost": (
            format_usage_cost(input_cost) if input_cost is not None else None
        ),
        "projected_cost_before_call": (
            _format_cost_value(projected_cost) if projected_cost is not None else None
        ),
        "enforced": input_cost is not None,
        "exceeded": projected_cost is not None and projected_cost > max_cost,
    }


def _format_cost_value(value: float | None) -> str | None:
    if value is None:
        return None
    return f"${value:.6f}"


def _cost_budget_block_status(cost_budget: dict) -> str | None:
    if not cost_budget["enforced"]:
        return "cost_budget_unavailable"
    if cost_budget["exceeded"]:
        return "cost_budget_exceeded"
    return None


def _print_cost_budget_block(cost_budget: dict) -> None:
    if not cost_budget["enforced"]:
        typer.secho(
            "Cannot enforce cost budget: set AGENT_ZERO_INPUT_COST_PER_1M_TOKENS.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo("No model call made.")
        return

    typer.secho(
        "Cost budget exceeded before model call: "
        f"projected={cost_budget['projected_cost_before_call']}, "
        f"max={cost_budget['max_cost']}.",
        fg=typer.colors.RED,
        err=True,
    )
    typer.echo("No model call made.")


def _enforce_retry_cost_budget(
    system_prompt: str,
    user_prompt: str,
    config,
    max_cost: float | None,
    spent_cost: float,
) -> None:
    cost_budget = _cost_budget_check(
        system_prompt,
        user_prompt,
        config,
        max_cost,
        spent_cost=spent_cost,
    )
    status = _cost_budget_block_status(cost_budget) if cost_budget else None
    if status is None:
        return

    _print_cost_budget_block(cost_budget)
    raise typer.Exit(code=1 if status == "cost_budget_exceeded" else 2)


def _tool_call_for_cost_budget(cost_budget: dict | None) -> dict:
    if cost_budget is None:
        return _tool_call_record(
            "check_cost_budget",
            "skipped",
            output_summary="no max_cost",
            duration_ms=0.0,
        )

    status = "success" if _cost_budget_block_status(cost_budget) is None else "failed"
    output_summary = (
        f"projected={cost_budget['projected_cost_before_call']}; "
        f"max={cost_budget['max_cost']}; "
        f"exceeded={cost_budget['exceeded']}"
    )
    if not cost_budget["enforced"]:
        output_summary = "input cost is not configured"
    return _tool_call_record(
        "check_cost_budget",
        status,
        output_summary=output_summary,
        duration_ms=0.0,
    )


def _model_calls_cost_value(model_calls: list[dict]) -> float:
    total = 0.0
    for call in model_calls:
        usage = call.get("usage", {})
        cost_value = _parse_cost_value(usage.get("estimated_cost"))
        if cost_value is not None:
            total += cost_value
    return total


def _finalize_cost_budget(
    cost_budget: dict | None,
    model_calls: list[dict],
) -> dict | None:
    if cost_budget is None:
        return None

    final_cost = _model_calls_cost_value(model_calls)
    finalized = dict(cost_budget)
    finalized["final_estimated_cost"] = (
        _format_cost_value(final_cost) if model_calls else None
    )
    finalized["final_exceeded"] = (
        final_cost > _parse_cost_value(cost_budget["max_cost"])
        if model_calls and _parse_cost_value(cost_budget["max_cost"]) is not None
        else False
    )
    finalized["model_calls"] = len(model_calls)
    return finalized


def _run_trace_json(
    mode: str,
    task: str,
    config,
    repository_context,
    status: str,
    success: bool,
    model_calls: list[dict] | None = None,
    changed_files: list[str] | None = None,
    patch_summary: list[dict] | None = None,
    validation: ValidationResult | None = None,
    classification: dict | None = None,
    dry_run: bool = False,
    retries: list[dict] | None = None,
    tool_calls: list[dict] | None = None,
    cost_budget: dict | None = None,
    error: str | None = None,
) -> dict:
    decision = repository_context.decision
    trace = {
        "mode": mode,
        "task": task,
        "provider": config.provider,
        "model": config.model,
        "status": status,
        "success": success,
        "context": {
            "query_terms": decision.query_terms,
            "target_files": decision.target_files,
            "selected_files": decision.selected_files,
            "included_files": decision.included_files,
            "skipped_files": decision.skipped_files,
            "truncated_files": decision.truncated_files,
            "focused_files": decision.focused_files,
            "context_budget_tokens": decision.context_budget_tokens,
            "context_content_tokens": decision.context_content_tokens,
            "index_used": decision.index_used,
            "memory_used": decision.memory_used,
            "sqlite_memory_used": decision.sqlite_memory_used,
            "reasons": decision.reasons,
        },
        "model_calls": model_calls or [],
        "changed_files": changed_files or [],
        "patch_summary": patch_summary or [],
        "validation": _validation_summary(validation),
        "classification": classification,
        "dry_run": dry_run,
        "retries": retries or [],
        "tool_calls": tool_calls or [],
        "cost_budget": cost_budget,
    }
    if error is not None:
        trace["error"] = error
    return trace


def _code_clarification_trace_json(
    task: str,
    classification,
    dry_run: bool,
    tool_calls: list[dict] | None = None,
) -> dict:
    return {
        "mode": "code",
        "task": task,
        "provider": None,
        "model": None,
        "status": "clarification_needed",
        "success": False,
        "classification": classification.to_dict(),
        "context": None,
        "model_calls": [],
        "changed_files": [],
        "patch_summary": [],
        "validation": None,
        "dry_run": dry_run,
        "retries": [],
        "tool_calls": tool_calls or [],
        "cost_budget": None,
    }


def _print_code_trace(message: str, trace_level: TraceLevel) -> None:
    if _trace_enabled(trace_level):
        typer.echo(f"Code trace: {message}")


def _trace_validation_result(
    result: ValidationResult | None,
    trace_level: TraceLevel,
) -> None:
    if not _trace_enabled(trace_level):
        return
    if result is None:
        _print_code_trace("Validation skipped.", trace_level)
    elif result.passed:
        _print_code_trace("Validation passed.", trace_level)
    elif result.timed_out:
        _print_code_trace("Validation timed out.", trace_level)
    else:
        _print_code_trace(
            f"Validation failed with exit code {result.exit_code}.",
            trace_level,
        )


def _print_model_response(
    response, config, system_prompt: str, user_prompt: str
) -> None:
    typer.echo(response.content)
    _print_usage(response, config, system_prompt, user_prompt)


def _print_usage(response, config, system_prompt: str, user_prompt: str) -> None:
    usage = _usage_summary(response, config, system_prompt, user_prompt)

    label = "Estimated tokens" if usage["estimated"] else "Tokens"
    typer.echo("")
    typer.echo(
        f"{label}: "
        f"input={usage['input_tokens']}, "
        f"output={usage['output_tokens']}, "
        f"total={usage['total_tokens']}"
    )

    if usage["estimated_cost"] is not None:
        typer.echo(f"Estimated cost: {usage['estimated_cost']}")


def _usage_summary(response, config, system_prompt: str, user_prompt: str):
    usage = resolve_token_usage(
        response=response,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=config.model,
    )
    cost = estimate_usage_cost(usage, config)
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "estimated": usage.estimated,
        "estimated_cost": format_usage_cost(cost) if cost is not None else None,
    }


def _record_memory(
    mode: str,
    task: str,
    selected_files: list[str],
    status: str,
    success: bool,
    usage: dict | None = None,
    useful_files: list[str] | None = None,
    changed_files: list[str] | None = None,
    patch_summary: list[dict] | None = None,
    validation_passed: bool | None = None,
) -> None:
    if os.environ.get("AGENT_ZERO_DISABLE_MEMORY"):
        return

    record = {
        "mode": mode,
        "task_terms": task_terms(task),
        "selected_files": selected_files,
        "useful_files": useful_files or [],
        "status": status,
        "success": success,
    }
    if usage is not None:
        record["usage"] = usage
    if changed_files is not None:
        record["changed_files"] = changed_files
    if patch_summary is not None:
        record["patch_summary"] = patch_summary
    if validation_passed is not None:
        record["validation_passed"] = validation_passed
    record["reflection"] = build_reflection(record)

    root = Path.cwd()
    append_memory_record(root, record)
    write_memory_candidate(root, record)


def _complete_with_config(system_prompt: str, user_prompt: str, config):
    try:
        return _call_model(system_prompt, user_prompt, config)
    except ModelClientError as exc:
        typer.secho(f"Model call failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _call_model(system_prompt: str, user_prompt: str, config):
    client = create_model_client(config)
    return client.complete(system_prompt, user_prompt)


@app.command("classify")
def classify_command(
    task: str = typer.Argument(..., help="Request text to classify."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON.",
    ),
) -> None:
    """Classify a request by action type, command route, and write risk."""
    classification = classify_task(task)
    data = classification.to_dict()

    if json_output:
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
        return

    typer.echo(f"Action type: {classification.action_type}")
    typer.echo(f"Recommended mode: {classification.recommended_mode}")
    typer.echo(f"Subcategory: {classification.subcategory}")
    typer.echo(f"Write intent: {classification.write_intent}")
    typer.echo(f"Specificity: {classification.specificity}")
    typer.echo(f"Requires clarification: {classification.requires_clarification}")
    if classification.missing_information:
        typer.echo("Missing information:")
        for item in classification.missing_information:
            typer.echo(f"- {item}")
    typer.echo(f"Confidence: {classification.confidence}")
    typer.echo(f"Reason: {classification.reason}")


def _code_request_needs_clarification(classification) -> bool:
    if classification.recommended_mode != "code":
        return False
    return classification.requires_clarification


def _print_clarification_needed(classification) -> None:
    typer.echo("Clarification needed:")
    for item in classification.missing_information:
        typer.echo(f"- {item}")
    typer.echo(f"Recommended mode: {classification.recommended_mode}")
    typer.echo(f"Subcategory: {classification.subcategory}")
    typer.echo("No model call made.")


@app.command()
def ask(
    task: str = typer.Argument(..., help="Question to answer about the repository."),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional path to a .env file.",
    ),
    show_context: bool = typer.Option(
        False,
        "--show-context",
        help="Print context selection reasons before the model answer.",
    ),
    context_budget: int = typer.Option(
        DEFAULT_CONTEXT_BUDGET_TOKENS,
        "--context-budget",
        min=1,
        help="Approximate token budget for selected file contents.",
    ),
    trace: bool = typer.Option(
        False,
        "--trace",
        help="Print the high-level agent execution timeline.",
    ),
    trace_level: str = typer.Option(
        "none",
        "--trace-level",
        help="Trace verbosity: none, basic, or debug.",
    ),
    trace_json: bool = typer.Option(
        False,
        "--trace-json",
        help="Print a machine-readable run trace after the answer.",
    ),
    max_cost: float | None = typer.Option(
        None,
        "--max-cost",
        min=0.0,
        help="Maximum estimated model cost allowed for this run.",
    ),
) -> None:
    """Answer a repo-aware question without editing files."""
    started_at = _timer_start()
    config = _load_config_or_exit(env_file)
    tool_calls = [
        _tool_call_record(
            "load_config",
            "success",
            output_summary=f"provider={config.provider}, model={config.model}",
            duration_ms=_duration_ms(started_at),
        )
    ]
    resolved_trace_level = _resolve_trace_level(trace, trace_level)
    started_at = _timer_start()
    user_prompt, repository_context = _build_user_prompt_with_context(
        "User question",
        task,
        context_budget_tokens=context_budget,
    )
    tool_calls.append(
        _tool_call_record(
            "build_repository_context",
            "success",
            input_summary=f"context_budget={context_budget}",
            output_summary=(
                f"selected={len(repository_context.decision.selected_files)}, "
                f"included={len(repository_context.decision.included_files)}"
            ),
            duration_ms=_duration_ms(started_at),
        )
    )
    if show_context:
        _print_context_selection(repository_context)
    if _trace_enabled(resolved_trace_level):
        _print_trace("ask", repository_context, config, resolved_trace_level)
    cost_budget = _cost_budget_check(
        ASK_SYSTEM_PROMPT,
        user_prompt,
        config,
        max_cost,
    )
    if cost_budget is not None:
        tool_calls.append(_tool_call_for_cost_budget(cost_budget))
    cost_budget_status = (
        _cost_budget_block_status(cost_budget) if cost_budget is not None else None
    )
    if cost_budget_status is not None:
        _print_cost_budget_block(cost_budget)
        started_at = _timer_start()
        _record_memory(
            mode="ask",
            task=task,
            selected_files=repository_context.decision.selected_files,
            status=cost_budget_status,
            success=False,
        )
        tool_calls.append(
            _tool_call_record(
                "record_memory",
                "success",
                output_summary=f"status={cost_budget_status}",
                duration_ms=_duration_ms(started_at),
            )
        )
        if trace_json:
            _print_trace_json(
                _run_trace_json(
                    mode="ask",
                    task=task,
                    config=config,
                    repository_context=repository_context,
                    status=cost_budget_status,
                    success=False,
                    model_calls=[],
                    tool_calls=tool_calls,
                    cost_budget=cost_budget,
                )
            )
        raise typer.Exit(code=1 if cost_budget_status == "cost_budget_exceeded" else 2)
    started_at = _timer_start()
    response = _complete_with_config(ASK_SYSTEM_PROMPT, user_prompt, config)
    tool_calls.append(
        _tool_call_record(
            "model.complete",
            "success",
            input_summary="purpose=initial",
            output_summary=f"chars={len(response.content)}",
            duration_ms=_duration_ms(started_at),
        )
    )
    model_calls = [
        _model_call_summary(
            purpose="initial",
            response=response,
            config=config,
            system_prompt=ASK_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
    ]
    _print_model_response(response, config, ASK_SYSTEM_PROMPT, user_prompt)
    usage = model_calls[0]["usage"]
    started_at = _timer_start()
    _record_memory(
        mode="ask",
        task=task,
        selected_files=repository_context.decision.selected_files,
        status="ask_completed",
        success=True,
        usage=usage,
    )
    tool_calls.append(
        _tool_call_record(
            "record_memory",
            "success",
            output_summary="status=ask_completed",
            duration_ms=_duration_ms(started_at),
        )
    )
    if trace_json:
        _print_trace_json(
            _run_trace_json(
                mode="ask",
                task=task,
                config=config,
                repository_context=repository_context,
                status="ask_completed",
                success=True,
                model_calls=model_calls,
                tool_calls=tool_calls,
                cost_budget=_finalize_cost_budget(cost_budget, model_calls),
            )
        )


@app.command()
def plan(
    task: str = typer.Argument(..., help="Change request to plan."),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional path to a .env file.",
    ),
    show_context: bool = typer.Option(
        False,
        "--show-context",
        help="Print context selection reasons before the model plan.",
    ),
    context_budget: int = typer.Option(
        DEFAULT_CONTEXT_BUDGET_TOKENS,
        "--context-budget",
        min=1,
        help="Approximate token budget for selected file contents.",
    ),
    trace: bool = typer.Option(
        False,
        "--trace",
        help="Print the high-level agent execution timeline.",
    ),
    trace_level: str = typer.Option(
        "none",
        "--trace-level",
        help="Trace verbosity: none, basic, or debug.",
    ),
    trace_json: bool = typer.Option(
        False,
        "--trace-json",
        help="Print a machine-readable run trace after the plan.",
    ),
    max_cost: float | None = typer.Option(
        None,
        "--max-cost",
        min=0.0,
        help="Maximum estimated model cost allowed for this run.",
    ),
) -> None:
    """Inspect and produce an implementation plan."""
    started_at = _timer_start()
    config = _load_config_or_exit(env_file)
    tool_calls = [
        _tool_call_record(
            "load_config",
            "success",
            output_summary=f"provider={config.provider}, model={config.model}",
            duration_ms=_duration_ms(started_at),
        )
    ]
    resolved_trace_level = _resolve_trace_level(trace, trace_level)
    started_at = _timer_start()
    user_prompt, repository_context = _build_user_prompt_with_context(
        "Change request",
        task,
        context_budget_tokens=context_budget,
    )
    tool_calls.append(
        _tool_call_record(
            "build_repository_context",
            "success",
            input_summary=f"context_budget={context_budget}",
            output_summary=(
                f"selected={len(repository_context.decision.selected_files)}, "
                f"included={len(repository_context.decision.included_files)}"
            ),
            duration_ms=_duration_ms(started_at),
        )
    )
    if show_context:
        _print_context_selection(repository_context)
    if _trace_enabled(resolved_trace_level):
        _print_trace("plan", repository_context, config, resolved_trace_level)
    cost_budget = _cost_budget_check(
        PLAN_SYSTEM_PROMPT,
        user_prompt,
        config,
        max_cost,
    )
    if cost_budget is not None:
        tool_calls.append(_tool_call_for_cost_budget(cost_budget))
    cost_budget_status = (
        _cost_budget_block_status(cost_budget) if cost_budget is not None else None
    )
    if cost_budget_status is not None:
        _print_cost_budget_block(cost_budget)
        started_at = _timer_start()
        _record_memory(
            mode="plan",
            task=task,
            selected_files=repository_context.decision.selected_files,
            status=cost_budget_status,
            success=False,
        )
        tool_calls.append(
            _tool_call_record(
                "record_memory",
                "success",
                output_summary=f"status={cost_budget_status}",
                duration_ms=_duration_ms(started_at),
            )
        )
        if trace_json:
            _print_trace_json(
                _run_trace_json(
                    mode="plan",
                    task=task,
                    config=config,
                    repository_context=repository_context,
                    status=cost_budget_status,
                    success=False,
                    model_calls=[],
                    tool_calls=tool_calls,
                    cost_budget=cost_budget,
                )
            )
        raise typer.Exit(code=1 if cost_budget_status == "cost_budget_exceeded" else 2)
    started_at = _timer_start()
    response = _complete_with_config(PLAN_SYSTEM_PROMPT, user_prompt, config)
    tool_calls.append(
        _tool_call_record(
            "model.complete",
            "success",
            input_summary="purpose=initial",
            output_summary=f"chars={len(response.content)}",
            duration_ms=_duration_ms(started_at),
        )
    )
    model_calls = [
        _model_call_summary(
            purpose="initial",
            response=response,
            config=config,
            system_prompt=PLAN_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
    ]
    _print_model_response(response, config, PLAN_SYSTEM_PROMPT, user_prompt)
    usage = model_calls[0]["usage"]
    started_at = _timer_start()
    _record_memory(
        mode="plan",
        task=task,
        selected_files=repository_context.decision.selected_files,
        status="plan_completed",
        success=True,
        usage=usage,
    )
    tool_calls.append(
        _tool_call_record(
            "record_memory",
            "success",
            output_summary="status=plan_completed",
            duration_ms=_duration_ms(started_at),
        )
    )
    if trace_json:
        _print_trace_json(
            _run_trace_json(
                mode="plan",
                task=task,
                config=config,
                repository_context=repository_context,
                status="plan_completed",
                success=True,
                model_calls=model_calls,
                tool_calls=tool_calls,
                cost_budget=_finalize_cost_budget(cost_budget, model_calls),
            )
        )


@app.command("index")
def index_command(
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Optional output path. Defaults to .agent-zero/index.json.",
    ),
) -> None:
    """Build a narrative repository index for context selection."""
    root = Path.cwd()
    index = build_repo_index(root)
    output_path = write_repo_index(root, output)

    typer.echo(f"Index written: {output_path}")
    typer.echo(f"Files indexed: {index_file_count(index)}")
    typer.echo(f"Relationships: {index_relationship_count(index)}")


@app.command("memory")
def memory_command(
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter SQLite memory items by status: candidate, confirmed, or rejected.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        min=1,
        help="Maximum SQLite memory items to print per group.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print memory summary as JSON.",
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Prune low-value SQLite memory. Defaults to rejected items.",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Reset curated SQLite memory. Requires --yes to delete.",
    ),
    include_raw: bool = typer.Option(
        False,
        "--include-raw",
        help="With --reset, also delete the raw JSONL audit log.",
    ),
    feedback: str | None = typer.Option(
        None,
        "--feedback",
        help="Apply user feedback to the latest memory item: worked or failed.",
    ),
    detect_feedback: str | None = typer.Option(
        None,
        "--detect-feedback",
        help="Detect feedback from text. Dry-run unless --yes is provided.",
    ),
    review: bool = typer.Option(
        False,
        "--review",
        help="Review candidate memory items with evidence and ids.",
    ),
    approve: str | None = typer.Option(
        None,
        "--approve",
        help="Approve a memory item by id prefix, or use latest.",
    ),
    reject: str | None = typer.Option(
        None,
        "--reject",
        help="Reject a memory item by id prefix, or use latest.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Confirm destructive memory maintenance actions.",
    ),
) -> None:
    """Inspect raw and curated local memory."""
    root = Path.cwd()
    raw_records = load_memory(root)
    memory_items = load_memory_items(root)
    valid_statuses = {"candidate", "confirmed", "rejected"}
    if status is not None:
        if status not in valid_statuses:
            typer.secho(
                "Invalid status. Use one of: candidate, confirmed, rejected.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        memory_items = [item for item in memory_items if item["status"] == status]

    if detect_feedback is not None:
        if feedback is not None or prune or reset or review or approve or reject:
            typer.secho(
                "--detect-feedback cannot be combined with other memory actions.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        _handle_memory_detect_feedback(
            root=root,
            text=detect_feedback,
            status=status,
            yes=yes,
            json_output=json_output,
        )
        return

    if feedback is not None:
        if prune or reset or review or approve or reject:
            typer.secho(
                "--feedback cannot be combined with other memory actions.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        _handle_memory_feedback(
            root=root,
            feedback=feedback,
            status=status,
            json_output=json_output,
        )
        return

    if review:
        if prune or reset or approve or reject:
            typer.secho(
                "--review cannot be combined with other memory actions.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        _handle_memory_review(
            memory_items=memory_items,
            json_output=json_output,
            limit=limit,
        )
        return

    if approve is not None or reject is not None:
        if prune or reset:
            typer.secho(
                "--approve/--reject cannot be combined with --prune or --reset.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        if approve is not None and reject is not None:
            typer.secho(
                "Use either --approve or --reject, not both.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        _handle_memory_approval(
            root=root,
            selector=approve if approve is not None else reject,
            approve=approve is not None,
            status=status,
            json_output=json_output,
        )
        return

    if prune:
        _handle_memory_prune(
            root=root,
            raw_record_count=len(raw_records),
            memory_items=memory_items,
            status=status or "rejected",
            yes=yes,
            json_output=json_output,
            limit=limit,
        )
        return

    if reset:
        _handle_memory_reset(
            root=root,
            raw_record_count=len(raw_records),
            memory_item_count=len(memory_items),
            include_raw=include_raw,
            yes=yes,
            json_output=json_output,
        )
        return

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "raw_memory_records": len(raw_records),
                    "sqlite_memory_items": len(memory_items),
                    "items": memory_items,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    typer.echo(f"Raw memory records: {len(raw_records)}")
    typer.echo(f"SQLite memory items: {len(memory_items)}")
    typer.echo("")
    for label, status_value in (
        ("Confirmed", "confirmed"),
        ("Candidates", "candidate"),
        ("Rejected", "rejected"),
    ):
        if status is not None and status != status_value:
            continue
        _print_memory_group(label, memory_items, status_value, limit)


def _handle_memory_prune(
    root: Path,
    raw_record_count: int,
    memory_items: list[dict],
    status: str,
    yes: bool,
    json_output: bool,
    limit: int,
) -> None:
    if status == "confirmed":
        typer.secho(
            "Refusing to prune confirmed memory. Confirmed lessons are protected.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    prunable_items = [item for item in memory_items if item["status"] == status]
    if json_output:
        deleted_count = (
            delete_memory_items(root, {status}) if yes and prunable_items else 0
        )
        typer.echo(
            json.dumps(
                {
                    "action": "prune",
                    "dry_run": not yes,
                    "status": status,
                    "raw_memory_records": raw_record_count,
                    "prunable_items": len(prunable_items),
                    "deleted_items": deleted_count,
                    "items": prunable_items,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if not yes:
        typer.echo("Dry run: no memory deleted.")
        typer.echo(f"Prunable {status} memory items: {len(prunable_items)}")
        typer.echo("")
        _print_memory_group("Would prune", prunable_items, status, limit)
        typer.echo("Re-run with --yes to delete these SQLite memory items.")
        return

    deleted_count = delete_memory_items(root, {status})
    typer.echo(f"Deleted {status} memory items: {deleted_count}")
    typer.echo("Confirmed memory kept.")


def _handle_memory_feedback(
    root: Path,
    feedback: str,
    status: str | None,
    json_output: bool,
) -> None:
    if feedback not in {"worked", "failed"}:
        typer.secho(
            "Invalid feedback. Use one of: worked, failed.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        item = apply_memory_feedback(root, feedback, status=status)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "action": "feedback",
                    "feedback": feedback,
                    "status_filter": status,
                    "updated": item is not None,
                    "item": item,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if item is None:
        typer.echo("No matching SQLite memory item found.")
        return

    typer.echo(f"Applied feedback: {feedback}")
    typer.echo(f"Updated memory status: {item['status']}")
    typer.echo(f"Confidence: {item['confidence']}")
    typer.echo(f"Claim: {item['claim']}")


def _handle_memory_review(
    memory_items: list[dict],
    json_output: bool,
    limit: int,
) -> None:
    candidates = [item for item in memory_items if item["status"] == "candidate"]
    candidates = candidates[-limit:]

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "action": "review",
                    "candidate_items": len(candidates),
                    "items": candidates,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    typer.echo(f"Candidate memory items: {len(candidates)}")
    if not candidates:
        typer.echo("- (none)")
        return

    for item in candidates:
        _print_memory_review_item(item)


def _handle_memory_approval(
    root: Path,
    selector: str | None,
    approve: bool,
    status: str | None,
    json_output: bool,
) -> None:
    if selector is None:
        selector = "latest"

    if approve:
        item = update_memory_item_status(
            root,
            selector=selector,
            next_status="confirmed",
            next_confidence="high",
            event_type="user_approved",
            status_filter=status,
        )
        action = "approve"
    else:
        item = update_memory_item_status(
            root,
            selector=selector,
            next_status="rejected",
            next_confidence="low",
            event_type="user_rejected",
            status_filter=status,
        )
        action = "reject"

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "action": action,
                    "selector": selector,
                    "updated": item is not None,
                    "item": item,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if item is None:
        typer.echo("No matching SQLite memory item found.")
        return

    action_label = "approved" if approve else "rejected"
    typer.echo(f"Memory {action_label}: {item['id']}")
    typer.echo(f"Updated memory status: {item['status']}")
    typer.echo(f"Confidence: {item['confidence']}")
    typer.echo(f"Claim: {item['claim']}")


def _print_memory_review_item(item: dict) -> None:
    typer.echo(f"- {item['id']} [{item.get('confidence', 'unknown')}]")
    typer.echo(f"  claim: {item.get('claim', '(no claim)')}")
    useful_files = item.get("useful_files") or []
    if useful_files:
        typer.echo(f"  files: {', '.join(useful_files)}")
    evidence = item.get("evidence") or []
    if evidence:
        typer.echo(f"  evidence: {', '.join(evidence)}")
    typer.echo("  approve: python -m agent_zero memory --approve " + item["id"][:8])
    typer.echo("  reject: python -m agent_zero memory --reject " + item["id"][:8])


def _handle_memory_detect_feedback(
    root: Path,
    text: str,
    status: str | None,
    yes: bool,
    json_output: bool,
) -> None:
    detected = detect_user_feedback(text)
    item = None
    if detected is not None and yes:
        item = apply_memory_feedback(root, detected, status=status)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "action": "detect_feedback",
                    "detected_feedback": detected,
                    "dry_run": not yes,
                    "status_filter": status,
                    "updated": item is not None,
                    "item": item,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if detected is None:
        typer.echo("Detected feedback: none")
        typer.echo("No memory updated.")
        return

    typer.echo(f"Detected feedback: {detected}")
    if not yes:
        typer.echo("Dry run: no memory updated.")
        typer.echo("Re-run with --yes to apply this feedback.")
        return

    if item is None:
        typer.echo("No matching SQLite memory item found.")
        return

    typer.echo(f"Updated memory status: {item['status']}")
    typer.echo(f"Confidence: {item['confidence']}")
    typer.echo(f"Claim: {item['claim']}")


def _handle_memory_reset(
    root: Path,
    raw_record_count: int,
    memory_item_count: int,
    include_raw: bool,
    yes: bool,
    json_output: bool,
) -> None:
    if json_output:
        deleted = (
            reset_memory(root, include_raw=include_raw)
            if yes
            else {"sqlite_items": 0, "raw_records": 0}
        )
        typer.echo(
            json.dumps(
                {
                    "action": "reset",
                    "dry_run": not yes,
                    "include_raw": include_raw,
                    "sqlite_items": memory_item_count,
                    "raw_memory_records": raw_record_count,
                    "deleted_sqlite_items": deleted["sqlite_items"],
                    "deleted_raw_records": deleted["raw_records"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if not yes:
        typer.echo("Dry run: no memory reset.")
        typer.echo(f"SQLite memory items that would be deleted: {memory_item_count}")
        typer.echo(
            "Raw memory records that would be deleted: "
            f"{raw_record_count if include_raw else 0}"
        )
        if not include_raw:
            typer.echo("Raw JSONL audit log would be kept.")
        typer.echo("Re-run with --yes to reset memory.")
        return

    deleted = reset_memory(root, include_raw=include_raw)
    typer.echo(f"Deleted SQLite memory items: {deleted['sqlite_items']}")
    if include_raw:
        typer.echo(f"Deleted raw memory records: {deleted['raw_records']}")
    else:
        typer.echo("Raw JSONL audit log kept.")


def _print_memory_group(
    label: str,
    memory_items: list[dict],
    status: str,
    limit: int,
) -> None:
    grouped_items = [item for item in memory_items if item["status"] == status]
    typer.echo(f"{label}:")
    if not grouped_items:
        typer.echo("- (none)")
        typer.echo("")
        return

    for item in grouped_items[-limit:]:
        confidence = item.get("confidence", "unknown")
        claim = item.get("claim", "(no claim)")
        typer.echo(f"- [{confidence}] {claim}")
        useful_files = item.get("useful_files") or []
        if useful_files:
            typer.echo(f"  files: {', '.join(useful_files)}")
        typer.echo(f"  use_count: {item.get('use_count', 0)}")
    typer.echo("")


@app.command()
def code(
    task: str = typer.Argument(..., help="Change request to implement."),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional path to a .env file.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the proposed patch without applying it.",
    ),
    context_budget: int = typer.Option(
        DEFAULT_CONTEXT_BUDGET_TOKENS,
        "--context-budget",
        min=1,
        help="Approximate token budget for selected file contents.",
    ),
    trace: bool = typer.Option(
        False,
        "--trace",
        help="Print the high-level agent execution timeline.",
    ),
    trace_level: str = typer.Option(
        "none",
        "--trace-level",
        help="Trace verbosity: none, basic, or debug.",
    ),
    trace_json: bool = typer.Option(
        False,
        "--trace-json",
        help="Print a machine-readable run trace after code execution.",
    ),
    max_cost: float | None = typer.Option(
        None,
        "--max-cost",
        min=0.0,
        help="Maximum estimated model cost allowed for this run.",
    ),
) -> None:
    """Apply a focused code change and validate it."""
    resolved_trace_level = _resolve_trace_level(trace, trace_level)
    started_at = _timer_start()
    classification = classify_task(task)
    tool_calls = [
        _tool_call_record(
            "classify_task",
            "success",
            input_summary=task,
            output_summary=(
                f"{classification.action_type}/"
                f"{classification.recommended_mode}; "
                f"requires_clarification={classification.requires_clarification}"
            ),
            duration_ms=_duration_ms(started_at),
        )
    ]
    if _code_request_needs_clarification(classification):
        _print_code_trace(
            "Clarification needed before context selection.", resolved_trace_level
        )
        _print_clarification_needed(classification)
        started_at = _timer_start()
        _record_memory(
            mode="code",
            task=task,
            selected_files=[],
            status="clarification_needed",
            success=False,
        )
        tool_calls.append(
            _tool_call_record(
                "record_memory",
                "success",
                output_summary="status=clarification_needed",
                duration_ms=_duration_ms(started_at),
            )
        )
        tool_calls.extend(
            [
                _tool_call_record(
                    "build_repository_context",
                    "skipped",
                    output_summary="clarification_needed",
                    duration_ms=0.0,
                ),
                _tool_call_record(
                    "model.complete",
                    "skipped",
                    output_summary="clarification_needed",
                    duration_ms=0.0,
                ),
            ]
        )
        if trace_json:
            _print_trace_json(
                _code_clarification_trace_json(
                    task=task,
                    classification=classification,
                    dry_run=dry_run,
                    tool_calls=tool_calls,
                )
            )
        raise typer.Exit(code=2)

    started_at = _timer_start()
    config = _load_config_or_exit(env_file)
    tool_calls.append(
        _tool_call_record(
            "load_config",
            "success",
            output_summary=f"provider={config.provider}, model={config.model}",
            duration_ms=_duration_ms(started_at),
        )
    )
    started_at = _timer_start()
    user_prompt, repository_context = _build_user_prompt_with_context(
        "Change request",
        task,
        context_budget_tokens=context_budget,
    )
    tool_calls.append(
        _tool_call_record(
            "build_repository_context",
            "success",
            input_summary=f"context_budget={context_budget}",
            output_summary=(
                f"selected={len(repository_context.decision.selected_files)}, "
                f"included={len(repository_context.decision.included_files)}"
            ),
            duration_ms=_duration_ms(started_at),
        )
    )
    if _trace_enabled(resolved_trace_level):
        _print_trace("code", repository_context, config, resolved_trace_level)
    active_system_prompt = CODE_SYSTEM_PROMPT
    active_user_prompt = user_prompt
    cost_budget = _cost_budget_check(
        active_system_prompt,
        active_user_prompt,
        config,
        max_cost,
    )
    if cost_budget is not None:
        tool_calls.append(_tool_call_for_cost_budget(cost_budget))
    cost_budget_status = (
        _cost_budget_block_status(cost_budget) if cost_budget is not None else None
    )
    if cost_budget_status is not None:
        _print_cost_budget_block(cost_budget)
        started_at = _timer_start()
        _record_memory(
            mode="code",
            task=task,
            selected_files=repository_context.decision.selected_files,
            status=cost_budget_status,
            success=False,
        )
        tool_calls.append(
            _tool_call_record(
                "record_memory",
                "success",
                output_summary=f"status={cost_budget_status}",
                duration_ms=_duration_ms(started_at),
            )
        )
        if trace_json:
            _print_trace_json(
                _run_trace_json(
                    mode="code",
                    task=task,
                    config=config,
                    repository_context=repository_context,
                    status=cost_budget_status,
                    success=False,
                    model_calls=[],
                    classification=classification.to_dict(),
                    dry_run=dry_run,
                    tool_calls=tool_calls,
                    cost_budget=cost_budget,
                )
            )
        raise typer.Exit(code=1 if cost_budget_status == "cost_budget_exceeded" else 2)
    started_at = _timer_start()
    response = _complete_with_config(active_system_prompt, active_user_prompt, config)
    _print_code_trace("Model response received.", resolved_trace_level)
    tool_calls.append(
        _tool_call_record(
            "model.complete",
            "success",
            input_summary="purpose=initial",
            output_summary=f"chars={len(response.content)}",
            duration_ms=_duration_ms(started_at),
        )
    )
    model_calls = [
        _model_call_summary(
            purpose="initial",
            response=response,
            config=config,
            system_prompt=active_system_prompt,
            user_prompt=active_user_prompt,
        )
    ]
    retries = []
    usage = model_calls[0]["usage"]

    try:
        started_at = _timer_start()
        diff_text = extract_unified_diff(response.content)
    except DiffExtractionError as exc:
        if is_no_change_response(response.content):
            tool_calls.append(
                _tool_call_record(
                    "extract_unified_diff",
                    "skipped",
                    output_summary="model indicated no changes",
                    duration_ms=0.0,
                )
            )
            _print_code_trace("Model indicated no changes.", resolved_trace_level)
            typer.echo("No changes applied.")
            typer.echo(response.content)
            _print_usage(response, config, active_system_prompt, active_user_prompt)
            started_at = _timer_start()
            _record_memory(
                mode="code",
                task=task,
                selected_files=repository_context.decision.selected_files,
                status="no_changes",
                success=True,
                usage=usage,
            )
            tool_calls.append(
                _tool_call_record(
                    "record_memory",
                    "success",
                    output_summary="status=no_changes",
                    duration_ms=_duration_ms(started_at),
                )
            )
            if trace_json:
                _print_trace_json(
                    _run_trace_json(
                        mode="code",
                        task=task,
                        config=config,
                        repository_context=repository_context,
                        status="no_changes",
                        success=True,
                        model_calls=model_calls,
                        classification=classification.to_dict(),
                        dry_run=dry_run,
                        tool_calls=tool_calls,
                        cost_budget=_finalize_cost_budget(cost_budget, model_calls),
                    )
                )
            return
        tool_calls.append(
            _tool_call_record(
                "extract_unified_diff",
                "failed",
                duration_ms=_duration_ms(started_at),
                error=str(exc),
            )
        )
        _print_code_trace("Diff extraction failed.", resolved_trace_level)
        typer.secho(f"Could not find a patch: {exc}", fg=typer.colors.RED, err=True)
        typer.echo(response.content)
        started_at = _timer_start()
        _record_memory(
            mode="code",
            task=task,
            selected_files=repository_context.decision.selected_files,
            status="diff_not_found",
            success=False,
            usage=usage,
        )
        tool_calls.append(
            _tool_call_record(
                "record_memory",
                "success",
                output_summary="status=diff_not_found",
                duration_ms=_duration_ms(started_at),
            )
        )
        if trace_json:
            _print_trace_json(
                _run_trace_json(
                    mode="code",
                    task=task,
                    config=config,
                    repository_context=repository_context,
                    status="diff_not_found",
                    success=False,
                    model_calls=model_calls,
                    classification=classification.to_dict(),
                    dry_run=dry_run,
                    tool_calls=tool_calls,
                    cost_budget=_finalize_cost_budget(cost_budget, model_calls),
                    error=str(exc),
                )
            )
        raise typer.Exit(code=1) from exc

    _print_code_trace("Extracted unified diff.", resolved_trace_level)
    tool_calls.append(
        _tool_call_record(
            "extract_unified_diff",
            "success",
            output_summary=f"chars={len(diff_text)}",
            duration_ms=_duration_ms(started_at),
        )
    )
    started_at = _timer_start()
    diff_summary = summarize_unified_diff(diff_text, root=Path.cwd())
    patch_summary = diff_summary_to_dicts(diff_summary)
    tool_calls.append(
        _tool_call_record(
            "summarize_unified_diff",
            "success",
            output_summary=f"files={len(patch_summary)}",
            duration_ms=_duration_ms(started_at),
        )
    )
    if not diff_summary_has_changes(diff_summary):
        _print_code_trace("Empty patch rejected.", resolved_trace_level)
        previous_empty_diff = diff_text
        started_at = _timer_start()
        retry_response, retry_diff_text, retry_diff_summary, retry_prompt = (
            _retry_after_empty_patch(
                task=task,
                user_prompt=user_prompt,
                previous_diff=previous_empty_diff,
                config=config,
                trace_level=resolved_trace_level,
                selected_files=repository_context.decision.selected_files,
                usage=usage,
                patch_summary=patch_summary,
                max_cost=max_cost,
                spent_cost=_model_calls_cost_value(model_calls),
            )
        )
        response = retry_response
        diff_text = retry_diff_text
        diff_summary = retry_diff_summary
        active_system_prompt = FIX_EMPTY_PATCH_SYSTEM_PROMPT
        active_user_prompt = retry_prompt
        usage = _usage_summary(
            response, config, active_system_prompt, active_user_prompt
        )
        model_calls.append(
            _model_call_summary(
                purpose="empty_patch_retry",
                response=response,
                config=config,
                system_prompt=active_system_prompt,
                user_prompt=active_user_prompt,
            )
        )
        retries.append({"type": "empty_patch", "status": "recovered"})
        tool_calls.append(
            _tool_call_record(
                "model.complete",
                "success",
                input_summary="purpose=empty_patch_retry",
                output_summary=f"chars={len(response.content)}",
                duration_ms=_duration_ms(started_at),
            )
        )
        patch_summary = diff_summary_to_dicts(diff_summary)
    _print_code_trace(
        f"Patch summary prepared for {len(patch_summary)} file(s).",
        resolved_trace_level,
    )
    if dry_run:
        _print_code_trace(
            "Dry run selected; patch application and validation skipped.",
            resolved_trace_level,
        )
        typer.echo("Dry run: no files changed.")
        _print_patch_summary(diff_summary)
        typer.echo("")
        typer.echo("Proposed patch:")
        typer.echo(diff_text)
        _print_usage(response, config, active_system_prompt, active_user_prompt)
        started_at = _timer_start()
        _record_memory(
            mode="code",
            task=task,
            selected_files=repository_context.decision.selected_files,
            status="dry_run",
            success=True,
            usage=usage,
            patch_summary=patch_summary,
        )
        tool_calls.extend(
            [
                _tool_call_record(
                    "apply_unified_diff",
                    "skipped",
                    output_summary="dry_run",
                    duration_ms=0.0,
                ),
                _tool_call_record(
                    "run_validation",
                    "skipped",
                    output_summary="dry_run",
                    duration_ms=0.0,
                ),
                _tool_call_record(
                    "record_memory",
                    "success",
                    output_summary="status=dry_run",
                    duration_ms=_duration_ms(started_at),
                ),
            ]
        )
        if trace_json:
            _print_trace_json(
                _run_trace_json(
                    mode="code",
                    task=task,
                    config=config,
                    repository_context=repository_context,
                    status="dry_run",
                    success=True,
                    model_calls=model_calls,
                    patch_summary=patch_summary,
                    classification=classification.to_dict(),
                    dry_run=True,
                    retries=retries,
                    tool_calls=tool_calls,
                    cost_budget=_finalize_cost_budget(cost_budget, model_calls),
                )
            )
        return

    try:
        started_at = _timer_start()
        patch_result = apply_unified_diff(Path.cwd(), diff_text)
        tool_calls.append(
            _tool_call_record(
                "apply_unified_diff",
                "success",
                output_summary=", ".join(patch_result.changed_files),
                duration_ms=_duration_ms(started_at),
            )
        )
    except PatchApplyError as exc:
        tool_calls.append(
            _tool_call_record(
                "apply_unified_diff",
                "failed",
                duration_ms=_duration_ms(started_at),
                error=str(exc),
            )
        )
        _print_code_trace("Patch application failed.", resolved_trace_level)
        started_at = _timer_start()
        (
            retry_response,
            retry_diff_text,
            retry_diff_summary,
            retry_patch_result,
            retry_prompt,
        ) = _retry_after_patch_application_failure(
            task=task,
            failed_diff=diff_text,
            failure=str(exc),
            failed_summary=diff_summary,
            config=config,
            trace_level=resolved_trace_level,
            selected_files=repository_context.decision.selected_files,
            usage=usage,
            patch_summary=patch_summary,
            max_cost=max_cost,
            spent_cost=_model_calls_cost_value(model_calls),
        )
        response = retry_response
        diff_text = retry_diff_text
        diff_summary = retry_diff_summary
        patch_result = retry_patch_result
        active_system_prompt = FIX_PATCH_APPLICATION_SYSTEM_PROMPT
        active_user_prompt = retry_prompt
        usage = _usage_summary(
            response, config, active_system_prompt, active_user_prompt
        )
        model_calls.append(
            _model_call_summary(
                purpose="patch_application_retry",
                response=response,
                config=config,
                system_prompt=active_system_prompt,
                user_prompt=active_user_prompt,
            )
        )
        retries.append({"type": "patch_application", "status": "recovered"})
        tool_calls.extend(
            [
                _tool_call_record(
                    "model.complete",
                    "success",
                    input_summary="purpose=patch_application_retry",
                    output_summary=f"chars={len(response.content)}",
                    duration_ms=_duration_ms(started_at),
                ),
                _tool_call_record(
                    "apply_unified_diff",
                    "success",
                    output_summary=", ".join(patch_result.changed_files),
                    duration_ms=0.0,
                ),
            ]
        )
        patch_summary = diff_summary_to_dicts(diff_summary)

    _print_code_trace(
        f"Applied patch to {', '.join(patch_result.changed_files)}.",
        resolved_trace_level,
    )
    typer.echo("Applied patch.")
    typer.echo("Changed files:")
    for changed_file in patch_result.changed_files:
        typer.echo(f"- {changed_file}")
    _print_patch_summary(diff_summary)

    started_at = _timer_start()
    validation_result = _run_validation(config)
    tool_calls.append(
        _tool_call_record(
            "run_validation",
            "success"
            if validation_result is None or validation_result.passed
            else "failed",
            output_summary=(
                "skipped"
                if validation_result is None
                else f"passed={validation_result.passed}"
            ),
            duration_ms=_duration_ms(started_at),
        )
    )
    _trace_validation_result(validation_result, resolved_trace_level)
    if not _print_validation_result(validation_result, exit_on_failure=False):
        _print_code_trace("Starting validation-fix retry.", resolved_trace_level)
        _retry_after_validation_failure(
            task=task,
            changed_files=patch_result.changed_files,
            validation_result=validation_result,
            config=config,
            trace_level=resolved_trace_level,
            max_cost=max_cost,
            spent_cost=_model_calls_cost_value(model_calls),
        )

    _print_usage(response, config, active_system_prompt, active_user_prompt)
    started_at = _timer_start()
    _record_memory(
        mode="code",
        task=task,
        selected_files=repository_context.decision.selected_files,
        status=(
            "validation_passed"
            if validation_result is not None and validation_result.passed
            else "completed"
        ),
        success=True,
        usage=usage,
        useful_files=patch_result.changed_files,
        changed_files=patch_result.changed_files,
        patch_summary=patch_summary,
        validation_passed=validation_result.passed if validation_result else None,
    )
    tool_calls.append(
        _tool_call_record(
            "record_memory",
            "success",
            output_summary=(
                "status=validation_passed"
                if validation_result is not None and validation_result.passed
                else "status=completed"
            ),
            duration_ms=_duration_ms(started_at),
        )
    )
    if trace_json:
        _print_trace_json(
            _run_trace_json(
                mode="code",
                task=task,
                config=config,
                repository_context=repository_context,
                status=(
                    "validation_passed"
                    if validation_result is not None and validation_result.passed
                    else "completed"
                ),
                success=True,
                model_calls=model_calls,
                changed_files=patch_result.changed_files,
                patch_summary=patch_summary,
                validation=validation_result,
                classification=classification.to_dict(),
                dry_run=False,
                retries=retries,
                tool_calls=tool_calls,
                cost_budget=_finalize_cost_budget(cost_budget, model_calls),
            )
        )


@app.command("eval")
def eval_command(
    target: str = typer.Argument(
        ...,
        help="Path to an eval JSON file, or task text when --mode is provided.",
    ),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="Run an ad-hoc eval with mode: ask, plan, or code.",
    ),
    output_dir: Path = typer.Option(
        Path("eval-results"),
        "--output-dir",
        help="Directory where eval result JSON files are written.",
    ),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional path to a .env file.",
    ),
    context_budget: int = typer.Option(
        DEFAULT_CONTEXT_BUDGET_TOKENS,
        "--context-budget",
        min=1,
        help="Approximate token budget for selected file contents.",
    ),
    show_context: bool = typer.Option(
        False,
        "--show-context",
        help="Print context selection reasons before running the eval.",
    ),
    expect: list[str] | None = typer.Option(
        None,
        "--expect",
        help="Expected term that should appear in the eval response. Repeatable.",
    ),
    forbid: list[str] | None = typer.Option(
        None,
        "--forbid",
        help="Forbidden term that should not appear in the eval response. Repeatable.",
    ),
) -> None:
    """Run one eval task and write a structured result."""
    config = _load_config_or_exit(env_file)

    if mode is None:
        try:
            spec = load_eval_spec(Path(target))
        except EvalSpecError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc
    else:
        if mode not in {"ask", "plan", "code"}:
            typer.secho(
                "Eval mode must be one of: ask, plan, code.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        spec = EvalSpec(
            name=_ad_hoc_eval_name(mode, target),
            mode=mode,  # type: ignore[arg-type]
            task=target.strip(),
            expected_terms=expect,
            forbidden_terms=forbid,
        )

    if spec.validation_command is not None:
        config = config.model_copy(
            update={"validation_command": spec.validation_command}
        )

    result = _run_eval(
        spec,
        config,
        context_budget_tokens=context_budget,
        show_context=show_context,
    )
    result_path = write_eval_result(result, output_dir)
    _record_memory(
        mode=f"eval:{spec.mode}",
        task=spec.task,
        selected_files=result.get("selected_files", []),
        status=result["status"],
        success=result["success"],
        usage=result["model_calls"][0]["usage"] if result["model_calls"] else None,
        useful_files=result.get("changed_files", []),
        changed_files=result.get("changed_files", []),
        patch_summary=result.get("patch_summary", []),
        validation_passed=(
            result["validation"]["passed"] if result.get("validation") else None
        ),
    )

    typer.echo(f"Eval: {spec.name}")
    typer.echo(f"Mode: {spec.mode}")
    typer.echo(f"Status: {result['status']}")
    typer.echo(f"Success: {result['success']}")
    if result.get("score") is not None:
        score = result["score"]
        typer.echo(
            "Score: "
            f"{score['passed_checks']}/{score['total_checks']} "
            f"(passed={score['passed']})"
        )
    typer.echo(f"Result file: {result_path}")

    if not result["success"]:
        raise typer.Exit(code=1)


@app.command("eval-suite")
def eval_suite_command(
    suite_file: Path = typer.Argument(..., help="Path to an eval suite JSON file."),
    output_dir: Path = typer.Option(
        Path("eval-results"),
        "--output-dir",
        help="Directory where eval result JSON files are written.",
    ),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional path to a .env file.",
    ),
    context_budget: int = typer.Option(
        DEFAULT_CONTEXT_BUDGET_TOKENS,
        "--context-budget",
        min=1,
        help="Approximate token budget for selected file contents.",
    ),
    allow_failures: bool = typer.Option(
        False,
        "--allow-failures",
        help="Exit zero even when one or more evals fail.",
    ),
) -> None:
    """Run a suite of eval tasks and write an aggregate result."""
    config = _load_config_or_exit(env_file)
    try:
        suite = load_eval_suite(suite_file)
    except EvalSpecError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    eval_summaries = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    total_cost = 0.0
    has_cost = False

    for spec in suite.evals:
        eval_config = config
        if spec.validation_command is not None:
            eval_config = config.model_copy(
                update={"validation_command": spec.validation_command}
            )

        result = _run_eval(
            spec,
            eval_config,
            context_budget_tokens=context_budget,
            show_context=False,
        )
        result_path = write_eval_result(result, output_dir)
        usage = _eval_result_usage(result)
        total_input_tokens += usage["input_tokens"]
        total_output_tokens += usage["output_tokens"]
        total_tokens += usage["total_tokens"]
        cost_value = _parse_cost_value(usage["estimated_cost"])
        if cost_value is not None:
            total_cost += cost_value
            has_cost = True

        score = result.get("score")
        score_passed = score is None or score.get("passed") is True
        eval_passed = result["success"] and score_passed
        eval_summaries.append(
            {
                "name": result["name"],
                "mode": result["mode"],
                "status": result["status"],
                "success": eval_passed,
                "run_success": result["success"],
                "score_passed": score_passed,
                "score": score,
                "result_file": str(result_path),
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
                "estimated_cost": usage["estimated_cost"],
            }
        )

    failed = [item for item in eval_summaries if not item["success"]]
    suite_result = {
        "name": suite.name,
        "total": len(eval_summaries),
        "passed": len(eval_summaries) - len(failed),
        "failed": len(failed),
        "success": not failed,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": f"${total_cost:.6f}" if has_cost else "(not available)",
        "evals": eval_summaries,
    }
    suite_result_path = write_eval_suite_result(
        suite_result,
        output_dir / "suites",
    )

    typer.echo(f"Eval suite: {suite.name}")
    typer.echo(f"Total: {suite_result['total']}")
    typer.echo(f"Passed: {suite_result['passed']}")
    typer.echo(f"Failed: {suite_result['failed']}")
    typer.echo(f"Total tokens: {suite_result['total_tokens']}")
    typer.echo(f"Estimated cost: {suite_result['estimated_cost']}")
    if failed:
        typer.echo("Failed evals:")
        for item in failed:
            reason = _eval_suite_failure_reason(item)
            typer.echo(f"- {item['name']} ({reason})")
    typer.echo(f"Result file: {suite_result_path}")

    if failed and not allow_failures:
        raise typer.Exit(code=1)


@app.command("eval-report")
def eval_report_command(
    output_dir: Path = typer.Option(
        Path("eval-results"),
        "--output-dir",
        help="Directory containing eval result JSON files.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Only include evals whose name contains this text.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        min=1,
        help="Maximum number of result files to show.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON.",
    ),
) -> None:
    """Summarize saved eval results without calling a model."""
    summaries = _load_eval_result_summaries(output_dir, name=name, limit=limit)

    if json_output:
        typer.echo(json.dumps(summaries, indent=2, sort_keys=True))
        return

    if not summaries:
        typer.echo(f"No eval results found in {output_dir}.")
        return

    typer.echo(f"Eval results: {output_dir}")
    typer.echo(f"Showing: {len(summaries)}")
    typer.echo("")
    for summary in summaries:
        typer.echo(f"- {summary['file']}")
        typer.echo(f"  name: {summary['name']}")
        typer.echo(f"  mode: {summary['mode']}")
        typer.echo(f"  status: {summary['status']}")
        typer.echo(f"  success: {summary['success']}")
        if summary["score"] is not None:
            typer.echo(f"  score: {summary['score']}")
        typer.echo(
            "  tokens: "
            f"input={summary['input_tokens']} "
            f"output={summary['output_tokens']} "
            f"total={summary['total_tokens']}"
        )
        typer.echo(f"  cost: {summary['estimated_cost']}")
        typer.echo(f"  selected files: {summary['selected_file_count']}")
        typer.echo(f"  changed files: {summary['changed_file_count']}")


def _load_eval_result_summaries(
    output_dir: Path,
    name: str | None = None,
    limit: int = 10,
) -> list[dict]:
    if not output_dir.exists():
        return []

    summaries = []
    for path in sorted(
        output_dir.glob("*.json"), key=lambda item: item.name, reverse=True
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        eval_name = str(data.get("name", ""))
        if name is not None and name not in eval_name:
            continue
        summaries.append(_eval_result_summary(path, data))
        if len(summaries) >= limit:
            break

    return summaries


def _eval_result_summary(path: Path, data: dict) -> dict:
    usage = _eval_result_usage(data)
    score = data.get("score")
    return {
        "file": path.name,
        "name": data.get("name", "(unknown)"),
        "mode": data.get("mode", "(unknown)"),
        "status": data.get("status", "(unknown)"),
        "success": data.get("success", False),
        "score": _format_eval_score(score) if score is not None else None,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "estimated_cost": usage["estimated_cost"],
        "selected_file_count": len(data.get("selected_files") or []),
        "changed_file_count": len(data.get("changed_files") or []),
    }


def _eval_result_usage(data: dict) -> dict:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cost_values = []

    for call in data.get("model_calls") or []:
        usage = call.get("usage") or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        cost_value = _parse_cost_value(usage.get("estimated_cost"))
        if cost_value is not None:
            cost_values.append(cost_value)

    estimated_cost = f"${sum(cost_values):.6f}" if cost_values else "(not available)"
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": estimated_cost,
    }


def _parse_cost_value(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if normalized.startswith("$"):
        normalized = normalized[1:]
    try:
        return float(normalized)
    except ValueError:
        return None


def _format_eval_score(score: dict) -> str:
    return (
        f"{score.get('passed_checks', 0)}/{score.get('total_checks', 0)} "
        f"passed={score.get('passed', False)}"
    )


def _eval_suite_failure_reason(summary: dict) -> str:
    if not summary.get("run_success", False):
        return summary.get("status", "run_failed")
    if summary.get("score_passed") is False:
        return "score_failed"
    return summary.get("status", "failed")


def _run_eval(
    spec: EvalSpec,
    config,
    context_budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    show_context: bool = False,
) -> dict:
    mode_details = _mode_details(spec.mode)
    user_prompt, repository_context = _build_user_prompt_with_context(
        mode_details["task_label"],
        spec.task,
        context_budget_tokens=context_budget_tokens,
    )
    if show_context:
        _print_context_selection(repository_context)
    selected_files = repository_context.decision.selected_files
    model_calls = []

    try:
        response = _call_model(mode_details["system_prompt"], user_prompt, config)
    except ModelClientError as exc:
        return _base_eval_result(
            spec=spec,
            config=config,
            success=False,
            status="model_failed",
            response=str(exc),
            selected_files=selected_files,
            model_calls=[],
        )

    model_calls.append(
        _model_call_summary(
            purpose="initial",
            response=response,
            config=config,
            system_prompt=mode_details["system_prompt"],
            user_prompt=user_prompt,
        )
    )

    if spec.mode in {"ask", "plan"}:
        return _base_eval_result(
            spec=spec,
            config=config,
            success=True,
            status=f"{spec.mode}_completed",
            response=response.content,
            selected_files=selected_files,
            model_calls=model_calls,
        )

    return _run_code_eval(
        spec=spec,
        config=config,
        initial_response=response,
        initial_user_prompt=user_prompt,
        selected_files=selected_files,
        model_calls=model_calls,
    )


def _ad_hoc_eval_name(mode: str, task: str) -> str:
    terms = task_terms(task)
    suffix = "-".join(terms[:6]) if terms else "task"
    return f"ad-hoc-{mode}-{suffix}"


def _run_code_eval(
    spec: EvalSpec,
    config,
    initial_response,
    initial_user_prompt: str,
    selected_files: list[str],
    model_calls: list[dict],
) -> dict:
    changed_files: list[str] = []
    diff_summary = []

    try:
        diff_text = extract_unified_diff(initial_response.content)
        diff_summary = summarize_unified_diff(diff_text, root=Path.cwd())
        if not diff_summary_has_changes(diff_summary):
            return _base_eval_result(
                spec=spec,
                config=config,
                success=False,
                status="empty_patch",
                response=initial_response.content,
                selected_files=selected_files,
                changed_files=changed_files,
                patch_summary=diff_summary,
                model_calls=model_calls,
                error="Model returned a diff with no file content changes.",
            )
        patch_result = apply_unified_diff(Path.cwd(), diff_text)
        changed_files.extend(patch_result.changed_files)
    except DiffExtractionError:
        if is_no_change_response(initial_response.content):
            return _base_eval_result(
                spec=spec,
                config=config,
                success=True,
                status="no_changes",
                response=initial_response.content,
                selected_files=selected_files,
                changed_files=changed_files,
                model_calls=model_calls,
            )
        return _base_eval_result(
            spec=spec,
            config=config,
            success=False,
            status="diff_not_found",
            response=initial_response.content,
            selected_files=selected_files,
            changed_files=changed_files,
            model_calls=model_calls,
        )
    except PatchApplyError as exc:
        return _base_eval_result(
            spec=spec,
            config=config,
            success=False,
            status="patch_failed",
            response=initial_response.content,
            selected_files=selected_files,
            changed_files=changed_files,
            patch_summary=diff_summary,
            model_calls=model_calls,
            error=str(exc),
        )

    validation_result = _validation_for_eval(config)
    if validation_result is None or validation_result.passed:
        return _base_eval_result(
            spec=spec,
            config=config,
            success=True,
            status="validation_passed",
            response=initial_response.content,
            selected_files=selected_files,
            changed_files=changed_files,
            patch_summary=diff_summary,
            validation=validation_result,
            model_calls=model_calls,
        )

    retry_result = _run_eval_retry(
        spec=spec,
        config=config,
        changed_files=changed_files,
        validation_result=validation_result,
        model_calls=model_calls,
    )

    return _base_eval_result(
        spec=spec,
        config=config,
        success=retry_result["success"],
        status=retry_result["status"],
        response=initial_response.content,
        selected_files=selected_files,
        changed_files=changed_files + retry_result["changed_files"],
        patch_summary=diff_summary,
        validation=retry_result["validation"],
        model_calls=model_calls,
        retry=retry_result["retry"],
    )


def _run_eval_retry(
    spec: EvalSpec,
    config,
    changed_files: list[str],
    validation_result: ValidationResult,
    model_calls: list[dict],
) -> dict:
    retry_prompt = _build_validation_retry_prompt(
        spec.task, changed_files, validation_result
    )

    try:
        retry_response = _call_model(
            FIX_VALIDATION_SYSTEM_PROMPT,
            retry_prompt,
            config,
        )
    except ModelClientError as exc:
        return {
            "success": False,
            "status": "retry_model_failed",
            "changed_files": [],
            "validation": validation_result,
            "retry": {"error": str(exc)},
        }

    model_calls.append(
        _model_call_summary(
            purpose="validation_retry",
            response=retry_response,
            config=config,
            system_prompt=FIX_VALIDATION_SYSTEM_PROMPT,
            user_prompt=retry_prompt,
        )
    )

    try:
        retry_diff_text = extract_unified_diff(retry_response.content)
        retry_diff_summary = summarize_unified_diff(retry_diff_text, root=Path.cwd())
        if not diff_summary_has_changes(retry_diff_summary):
            return {
                "success": False,
                "status": "retry_empty_patch",
                "changed_files": [],
                "validation": validation_result,
                "retry": {
                    "response": retry_response.content,
                    "patch_summary": diff_summary_to_dicts(retry_diff_summary),
                    "error": "Model returned a diff with no file content changes.",
                },
            }
        retry_patch_result = apply_unified_diff(Path.cwd(), retry_diff_text)
    except (DiffExtractionError, PatchApplyError) as exc:
        return {
            "success": False,
            "status": "retry_patch_failed",
            "changed_files": [],
            "validation": validation_result,
            "retry": {
                "response": retry_response.content,
                "error": str(exc),
            },
        }

    retry_validation_result = _validation_for_eval(config)
    return {
        "success": retry_validation_result is None or retry_validation_result.passed,
        "status": (
            "retry_validation_passed"
            if retry_validation_result is None or retry_validation_result.passed
            else "retry_validation_failed"
        ),
        "changed_files": retry_patch_result.changed_files,
        "validation": retry_validation_result,
        "retry": {
            "response": retry_response.content,
            "changed_files": retry_patch_result.changed_files,
            "patch_summary": diff_summary_to_dicts(retry_diff_summary),
        },
    }


def _validation_for_eval(config) -> ValidationResult | None:
    try:
        return _run_validation_command(config)
    except CommandRunError as exc:
        return ValidationResult(
            steps=[
                ValidationStepResult(
                    label="validation",
                    result=CommandResult(
                        command=[],
                        exit_code=1,
                        stdout="",
                        stderr=str(exc),
                    ),
                )
            ]
        )


def _mode_details(mode: str) -> dict[str, str]:
    if mode == "ask":
        return {"system_prompt": ASK_SYSTEM_PROMPT, "task_label": "User question"}
    if mode == "plan":
        return {"system_prompt": PLAN_SYSTEM_PROMPT, "task_label": "Change request"}
    return {"system_prompt": CODE_SYSTEM_PROMPT, "task_label": "Change request"}


def _model_call_summary(
    purpose: str,
    response,
    config,
    system_prompt: str,
    user_prompt: str,
) -> dict:
    return {
        "purpose": purpose,
        "usage": _usage_summary(response, config, system_prompt, user_prompt),
    }


def _base_eval_result(
    spec: EvalSpec,
    config,
    success: bool,
    status: str,
    response: str,
    model_calls: list[dict],
    selected_files: list[str] | None = None,
    changed_files: list[str] | None = None,
    patch_summary: list | None = None,
    validation: ValidationResult | None = None,
    retry: dict | None = None,
    error: str | None = None,
) -> dict:
    result = {
        "name": spec.name,
        "mode": spec.mode,
        "task": spec.task,
        "provider": config.provider,
        "model": config.model,
        "success": success,
        "status": status,
        "selected_files": selected_files or [],
        "changed_files": changed_files or [],
        "patch_summary": diff_summary_to_dicts(patch_summary or []),
        "validation": _validation_summary(validation),
        "model_calls": model_calls,
        "response": response,
    }

    if retry is not None:
        result["retry"] = retry
    if error is not None:
        result["error"] = error

    score = _score_eval_response(spec, response)
    if score is not None:
        result["score"] = score

    return result


def _score_eval_response(spec: EvalSpec, response: str) -> dict | None:
    expected_terms = spec.expected_terms or []
    forbidden_terms = spec.forbidden_terms or []
    if not expected_terms and not forbidden_terms:
        return None

    lowered_response = response.lower()
    missing_expected = [
        term for term in expected_terms if term.lower() not in lowered_response
    ]
    present_forbidden = [
        term for term in forbidden_terms if term.lower() in lowered_response
    ]
    passed_checks = (
        len(expected_terms)
        - len(missing_expected)
        + len(forbidden_terms)
        - len(present_forbidden)
    )
    total_checks = len(expected_terms) + len(forbidden_terms)
    return {
        "passed": not missing_expected and not present_forbidden,
        "passed_checks": passed_checks,
        "total_checks": total_checks,
        "expected_terms": expected_terms,
        "missing_expected_terms": missing_expected,
        "forbidden_terms": forbidden_terms,
        "present_forbidden_terms": present_forbidden,
    }


def _print_patch_summary(summary) -> None:
    typer.echo("")
    typer.echo("Patch summary:")
    typer.echo(format_diff_summary(summary))


def _validation_summary(result: ValidationResult | None) -> dict | None:
    if result is None:
        return None

    return {
        "command": result.command,
        "exit_code": result.exit_code,
        "passed": result.passed,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "steps": [
            {
                "label": step.label,
                "command": step.result.command,
                "exit_code": step.result.exit_code,
                "passed": step.result.passed,
                "timed_out": step.result.timed_out,
                "stdout": step.result.stdout,
                "stderr": step.result.stderr,
            }
            for step in result.steps
        ],
    }


def _run_validation(config) -> ValidationResult | None:
    try:
        return _run_validation_command(config)
    except CommandRunError as exc:
        typer.secho(f"Validation could not start: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _run_validation_command(config) -> ValidationResult | None:
    validation_commands = _validation_commands(config)
    if not validation_commands:
        return None

    steps = []
    for label, command in validation_commands:
        result = run_command(
            command,
            cwd=Path.cwd(),
            timeout_seconds=config.validation_timeout_seconds,
        )
        steps.append(ValidationStepResult(label=label, result=result))
        if not result.passed:
            break

    return ValidationResult(steps=steps)


def _validation_commands(config) -> list[tuple[str, str]]:
    if config.validation_command:
        return [("validation", config.validation_command)]

    commands = []
    if config.test_command:
        commands.append(("tests", config.test_command))
    if config.lint_command:
        commands.append(("lint", config.lint_command))
    if config.format_command:
        commands.append(("format", config.format_command))
    return commands


def _retry_after_empty_patch(
    task: str,
    user_prompt: str,
    previous_diff: str,
    config,
    trace_level: TraceLevel,
    selected_files: list[str],
    usage: dict,
    patch_summary: list[dict],
    max_cost: float | None = None,
    spent_cost: float = 0.0,
):
    typer.echo("")
    typer.echo("Attempting one empty-patch retry.")
    retry_prompt = _build_empty_patch_retry_prompt(task, user_prompt, previous_diff)
    _enforce_retry_cost_budget(
        FIX_EMPTY_PATCH_SYSTEM_PROMPT,
        retry_prompt,
        config,
        max_cost,
        spent_cost,
    )
    response = _complete_with_config(
        FIX_EMPTY_PATCH_SYSTEM_PROMPT,
        retry_prompt,
        config,
    )
    _print_code_trace("Empty-patch retry model response received.", trace_level)

    try:
        diff_text = extract_unified_diff(response.content)
    except DiffExtractionError as exc:
        _print_code_trace("Empty-patch retry diff extraction failed.", trace_level)
        retry_usage = _usage_summary(
            response,
            config,
            FIX_EMPTY_PATCH_SYSTEM_PROMPT,
            retry_prompt,
        )
        typer.secho(
            f"Could not find a retry patch: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(response.content)
        _record_memory(
            mode="code",
            task=task,
            selected_files=selected_files,
            status="empty_patch_retry_diff_not_found",
            success=False,
            usage=retry_usage,
            patch_summary=patch_summary,
        )
        raise typer.Exit(code=1) from exc

    _print_code_trace("Extracted empty-patch retry diff.", trace_level)
    diff_summary = summarize_unified_diff(diff_text, root=Path.cwd())
    if not diff_summary_has_changes(diff_summary):
        _print_code_trace("Empty-patch retry returned empty patch.", trace_level)
        retry_usage = _usage_summary(
            response,
            config,
            FIX_EMPTY_PATCH_SYSTEM_PROMPT,
            retry_prompt,
        )
        retry_patch_summary = diff_summary_to_dicts(diff_summary)
        typer.secho(
            "Empty patch: Retry also returned a diff with no file content changes.",
            fg=typer.colors.RED,
            err=True,
        )
        _record_memory(
            mode="code",
            task=task,
            selected_files=selected_files,
            status="empty_patch",
            success=False,
            usage=retry_usage or usage,
            patch_summary=retry_patch_summary or patch_summary,
        )
        raise typer.Exit(code=1)

    _print_code_trace("Empty-patch retry produced a non-empty patch.", trace_level)
    return response, diff_text, diff_summary, retry_prompt


def _build_empty_patch_retry_prompt(
    task: str,
    user_prompt: str,
    previous_diff: str,
) -> str:
    return (
        f"{user_prompt}\n\n"
        "The previous model response was rejected because it returned a unified "
        "diff with zero additions and zero deletions. That diff would not change "
        "any file.\n\n"
        f"Original change request:\n{task}\n\n"
        f"Rejected empty diff:\n```diff\n{previous_diff}\n```\n\n"
        "Return one corrected unified diff only. The corrected diff must include "
        "at least one added or removed content line. If the repository already "
        "satisfies the request, explain that without returning a diff."
    )


def _retry_after_patch_application_failure(
    task: str,
    failed_diff: str,
    failure: str,
    failed_summary,
    config,
    trace_level: TraceLevel,
    selected_files: list[str],
    usage: dict,
    patch_summary: list[dict],
    max_cost: float | None = None,
    spent_cost: float = 0.0,
):
    typer.echo("")
    typer.echo("Attempting one patch-application retry.")
    retry_prompt = _build_patch_application_retry_prompt(
        task=task,
        failed_diff=failed_diff,
        failure=failure,
        failed_summary=failed_summary,
    )
    _enforce_retry_cost_budget(
        FIX_PATCH_APPLICATION_SYSTEM_PROMPT,
        retry_prompt,
        config,
        max_cost,
        spent_cost,
    )
    response = _complete_with_config(
        FIX_PATCH_APPLICATION_SYSTEM_PROMPT,
        retry_prompt,
        config,
    )
    _print_code_trace("Patch-application retry model response received.", trace_level)

    try:
        diff_text = extract_unified_diff(response.content)
    except DiffExtractionError as exc:
        _print_code_trace(
            "Patch-application retry diff extraction failed.", trace_level
        )
        retry_usage = _usage_summary(
            response,
            config,
            FIX_PATCH_APPLICATION_SYSTEM_PROMPT,
            retry_prompt,
        )
        typer.secho(
            f"Could not find a retry patch: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(response.content)
        _record_memory(
            mode="code",
            task=task,
            selected_files=selected_files,
            status="patch_retry_diff_not_found",
            success=False,
            usage=retry_usage,
            patch_summary=patch_summary,
        )
        raise typer.Exit(code=1) from exc

    _print_code_trace("Extracted patch-application retry diff.", trace_level)
    diff_summary = summarize_unified_diff(diff_text, root=Path.cwd())
    retry_patch_summary = diff_summary_to_dicts(diff_summary)
    if not diff_summary_has_changes(diff_summary):
        _print_code_trace("Patch-application retry returned empty patch.", trace_level)
        retry_usage = _usage_summary(
            response,
            config,
            FIX_PATCH_APPLICATION_SYSTEM_PROMPT,
            retry_prompt,
        )
        typer.secho(
            "Empty patch: Patch-application retry returned no file content changes.",
            fg=typer.colors.RED,
            err=True,
        )
        _record_memory(
            mode="code",
            task=task,
            selected_files=selected_files,
            status="patch_retry_empty_patch",
            success=False,
            usage=retry_usage or usage,
            patch_summary=retry_patch_summary or patch_summary,
        )
        raise typer.Exit(code=1)

    try:
        patch_result = apply_unified_diff(Path.cwd(), diff_text)
    except PatchApplyError as exc:
        _print_code_trace("Patch-application retry failed.", trace_level)
        retry_usage = _usage_summary(
            response,
            config,
            FIX_PATCH_APPLICATION_SYSTEM_PROMPT,
            retry_prompt,
        )
        typer.secho(f"Patch failed after retry: {exc}", fg=typer.colors.RED, err=True)
        _record_memory(
            mode="code",
            task=task,
            selected_files=selected_files,
            status="patch_failed",
            success=False,
            usage=retry_usage or usage,
            patch_summary=retry_patch_summary or patch_summary,
        )
        raise typer.Exit(code=1) from exc

    _print_code_trace(
        f"Patch-application retry applied patch to "
        f"{', '.join(patch_result.changed_files)}.",
        trace_level,
    )
    return response, diff_text, diff_summary, patch_result, retry_prompt


def _build_patch_application_retry_prompt(
    task: str,
    failed_diff: str,
    failure: str,
    failed_summary,
) -> str:
    return (
        f"Original change request:\n{task}\n\n"
        f"Patch failure:\n{failure}\n\n"
        f"Rejected diff:\n```diff\n{failed_diff}\n```\n\n"
        "Current file excerpts:\n"
        f"{_patch_retry_file_context(failed_summary, failed_diff)}\n\n"
        "Return one corrected unified diff only. Use exact current file lines "
        "from the excerpts when writing context lines. Do not include line "
        "numbers in the diff."
    )


def _patch_retry_file_context(failed_summary, failed_diff: str) -> str:
    needles = _diff_context_needles(failed_diff)
    sections = []
    for summary in failed_summary[:3]:
        path = summary.path
        if path == "/dev/null":
            continue
        file_path = Path.cwd() / path
        if not file_path.exists():
            sections.append(f"### {path}\n(file does not exist)")
            continue

        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
        sections.append(f"### {path}\n{_line_numbered_windows(lines, needles)}")

    return "\n\n".join(sections) or "(no current file context available)"


def _diff_context_needles(diff_text: str) -> list[str]:
    needles = []
    for line in diff_text.splitlines():
        if line[:1] not in {" ", "-"}:
            continue
        if line.startswith("--- "):
            continue
        needle = line[1:].strip()
        if len(needle) >= 6 and needle not in needles:
            needles.append(needle)
    return needles


def _line_numbered_windows(lines: list[str], needles: list[str]) -> str:
    if len(lines) <= 160:
        return _format_line_window(lines, 0, len(lines))

    windows = []
    for needle in needles:
        for index, line in enumerate(lines):
            if needle in line:
                windows.append((max(index - 6, 0), min(index + 7, len(lines))))
                break

    windows.append((max(len(lines) - 60, 0), len(lines)))
    merged = _merge_windows(windows)
    return "\n---\n".join(
        _format_line_window(lines, start, end) for start, end in merged
    )


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged = []
    for start, end in sorted(windows):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _format_line_window(lines: list[str], start: int, end: int) -> str:
    return "".join(f"{index + 1}: {lines[index]}" for index in range(start, end))


def _retry_after_validation_failure(
    task: str,
    changed_files: list[str],
    validation_result: ValidationResult | None,
    config,
    trace_level: TraceLevel = "none",
    max_cost: float | None = None,
    spent_cost: float = 0.0,
) -> None:
    if validation_result is None:
        return

    typer.echo("")
    typer.echo("Attempting one validation-fix retry.")
    retry_prompt = _build_validation_retry_prompt(
        task, changed_files, validation_result
    )
    _enforce_retry_cost_budget(
        FIX_VALIDATION_SYSTEM_PROMPT,
        retry_prompt,
        config,
        max_cost,
        spent_cost,
    )
    response = _complete_with_config(FIX_VALIDATION_SYSTEM_PROMPT, retry_prompt, config)
    _print_code_trace("Retry model response received.", trace_level)

    try:
        diff_text = extract_unified_diff(response.content)
        patch_result = apply_unified_diff(Path.cwd(), diff_text)
    except DiffExtractionError as exc:
        _print_code_trace("Retry diff extraction failed.", trace_level)
        typer.secho(
            f"Could not find a retry patch: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(response.content)
        raise typer.Exit(code=1) from exc
    except PatchApplyError as exc:
        _print_code_trace("Retry patch application failed.", trace_level)
        typer.secho(f"Retry patch failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    _print_code_trace(
        f"Applied retry patch to {', '.join(patch_result.changed_files)}.",
        trace_level,
    )
    typer.echo("Applied retry patch.")
    typer.echo("Changed files:")
    for changed_file in patch_result.changed_files:
        typer.echo(f"- {changed_file}")

    retry_validation_result = _run_validation(config)
    _trace_validation_result(retry_validation_result, trace_level)
    _print_validation_result(retry_validation_result, exit_on_failure=True)


def _build_validation_retry_prompt(
    task: str,
    changed_files: list[str],
    validation_result: ValidationResult,
) -> str:
    return (
        f"Original change request:\n{task}\n\n"
        f"Changed files:\n{chr(10).join(f'- {path}' for path in changed_files)}\n\n"
        f"Validation command:\n{' '.join(validation_result.command)}\n\n"
        f"Exit code:\n{validation_result.exit_code}\n\n"
        f"Timed out:\n{validation_result.timed_out}\n\n"
        f"Stdout:\n{validation_result.stdout}\n\n"
        f"Stderr:\n{validation_result.stderr}"
    )


def _print_validation_result(
    result: ValidationResult | None,
    exit_on_failure: bool = True,
) -> bool:
    if result is None:
        typer.echo("")
        typer.echo("Validation skipped: AGENT_ZERO_VALIDATION_COMMAND is not set.")
        return True

    for step in result.steps:
        typer.echo("")
        typer.echo(f"Validation step: {step.label}")
        typer.echo(f"Validation command: {' '.join(step.result.command)}")
        if step.result.passed:
            typer.echo("Validation passed.")
            continue

        if step.result.timed_out:
            typer.echo("Validation timed out.")
        else:
            typer.echo(f"Validation failed with exit code {step.result.exit_code}.")

        if step.result.stdout:
            typer.echo("")
            typer.echo("Validation stdout:")
            typer.echo(step.result.stdout)
        if step.result.stderr:
            typer.echo("")
            typer.echo("Validation stderr:")
            typer.echo(step.result.stderr)

        if exit_on_failure:
            raise typer.Exit(code=1)
        return False

    return True
