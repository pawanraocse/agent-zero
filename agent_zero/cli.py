from pathlib import Path

import typer

from agent_zero.config import ConfigError, load_config
from agent_zero.context import build_repository_context
from agent_zero.diff_parser import (
    DiffExtractionError,
    extract_unified_diff,
    is_no_change_response,
)
from agent_zero.evals import EvalSpec, EvalSpecError, load_eval_spec, write_eval_result
from agent_zero.model_client import ModelClientError, create_model_client
from agent_zero.tools.command_tool import CommandRunError, CommandResult, run_command
from agent_zero.tools.patch_tool import PatchApplyError, apply_unified_diff
from agent_zero.usage import estimate_usage_cost, format_usage_cost, resolve_token_usage

app = typer.Typer(
    help="Agent Zero: a minimal coding agent built from scratch.",
    no_args_is_help=True,
)

ASK_SYSTEM_PROMPT = """You are Agent Zero, a minimal coding-agent learning project.
Answer questions using the provided repository context.
Be clear about what the context does and does not show.
Do not claim you edited files or ran validation.
When useful, mention relevant file paths from the context."""

PLAN_SYSTEM_PROMPT = """You are Agent Zero in plan mode.
Inspect the provided repository context and produce a structured implementation plan.
Do not edit files. Do not claim you ran validation.
Prefer current code evidence over future-looking documentation when they conflict.

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


def _build_user_prompt(task_label: str, task: str) -> str:
    repository_context = build_repository_context(Path.cwd(), task)
    return (
        f"{task_label}:\n{task}\n\n"
        f"Repository context:\n{repository_context.to_prompt()}"
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


def _complete_or_exit(
    system_prompt: str,
    user_prompt: str,
    env_file: Path | None,
):
    config = _load_config_or_exit(env_file)
    response = _complete_with_config(system_prompt, user_prompt, config)
    return response, config


def _complete_with_config(system_prompt: str, user_prompt: str, config):
    try:
        return _call_model(system_prompt, user_prompt, config)
    except ModelClientError as exc:
        typer.secho(f"Model call failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _call_model(system_prompt: str, user_prompt: str, config):
    client = create_model_client(config)
    return client.complete(system_prompt, user_prompt)


@app.command()
def ask(
    task: str = typer.Argument(..., help="Question to answer about the repository."),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional path to a .env file.",
    ),
) -> None:
    """Answer a repo-aware question without editing files."""
    user_prompt = _build_user_prompt("User question", task)
    response, config = _complete_or_exit(ASK_SYSTEM_PROMPT, user_prompt, env_file)
    _print_model_response(response, config, ASK_SYSTEM_PROMPT, user_prompt)


@app.command()
def plan(
    task: str = typer.Argument(..., help="Change request to plan."),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional path to a .env file.",
    ),
) -> None:
    """Inspect and produce an implementation plan."""
    user_prompt = _build_user_prompt("Change request", task)
    response, config = _complete_or_exit(PLAN_SYSTEM_PROMPT, user_prompt, env_file)
    _print_model_response(response, config, PLAN_SYSTEM_PROMPT, user_prompt)


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
) -> None:
    """Apply a focused code change and validate it."""
    config = _load_config_or_exit(env_file)
    user_prompt = _build_user_prompt("Change request", task)
    response = _complete_with_config(CODE_SYSTEM_PROMPT, user_prompt, config)

    try:
        diff_text = extract_unified_diff(response.content)
    except DiffExtractionError as exc:
        if is_no_change_response(response.content):
            typer.echo("No changes applied.")
            typer.echo(response.content)
            _print_usage(response, config, CODE_SYSTEM_PROMPT, user_prompt)
            return
        typer.secho(f"Could not find a patch: {exc}", fg=typer.colors.RED, err=True)
        typer.echo(response.content)
        raise typer.Exit(code=1) from exc

    if dry_run:
        typer.echo("Dry run: no files changed.")
        typer.echo("")
        typer.echo("Proposed patch:")
        typer.echo(diff_text)
        _print_usage(response, config, CODE_SYSTEM_PROMPT, user_prompt)
        return

    try:
        patch_result = apply_unified_diff(Path.cwd(), diff_text)
    except PatchApplyError as exc:
        typer.secho(f"Patch failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Applied patch.")
    typer.echo("Changed files:")
    for changed_file in patch_result.changed_files:
        typer.echo(f"- {changed_file}")

    validation_result = _run_validation(config)
    if not _print_validation_result(validation_result, exit_on_failure=False):
        _retry_after_validation_failure(
            task=task,
            changed_files=patch_result.changed_files,
            validation_result=validation_result,
            config=config,
        )

    _print_usage(response, config, CODE_SYSTEM_PROMPT, user_prompt)


@app.command("eval")
def eval_command(
    spec_path: Path = typer.Argument(..., help="Path to an eval JSON file."),
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
) -> None:
    """Run one eval task and write a structured result."""
    config = _load_config_or_exit(env_file)

    try:
        spec = load_eval_spec(spec_path)
    except EvalSpecError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if spec.validation_command is not None:
        config = config.model_copy(
            update={"validation_command": spec.validation_command}
        )

    result = _run_eval(spec, config)
    result_path = write_eval_result(result, output_dir)

    typer.echo(f"Eval: {spec.name}")
    typer.echo(f"Mode: {spec.mode}")
    typer.echo(f"Status: {result['status']}")
    typer.echo(f"Success: {result['success']}")
    typer.echo(f"Result file: {result_path}")

    if not result["success"]:
        raise typer.Exit(code=1)


def _run_eval(spec: EvalSpec, config) -> dict:
    mode_details = _mode_details(spec.mode)
    user_prompt = _build_user_prompt(mode_details["task_label"], spec.task)
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
            model_calls=model_calls,
        )

    return _run_code_eval(
        spec=spec,
        config=config,
        initial_response=response,
        initial_user_prompt=user_prompt,
        model_calls=model_calls,
    )


def _run_code_eval(
    spec: EvalSpec,
    config,
    initial_response,
    initial_user_prompt: str,
    model_calls: list[dict],
) -> dict:
    changed_files: list[str] = []

    try:
        diff_text = extract_unified_diff(initial_response.content)
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
                changed_files=changed_files,
                model_calls=model_calls,
            )
        return _base_eval_result(
            spec=spec,
            config=config,
            success=False,
            status="diff_not_found",
            response=initial_response.content,
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
            changed_files=changed_files,
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
            changed_files=changed_files,
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
        changed_files=changed_files + retry_result["changed_files"],
        validation=retry_result["validation"],
        model_calls=model_calls,
        retry=retry_result["retry"],
    )


def _run_eval_retry(
    spec: EvalSpec,
    config,
    changed_files: list[str],
    validation_result: CommandResult,
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
        },
    }


def _validation_for_eval(config) -> CommandResult | None:
    try:
        return _run_validation_command(config)
    except CommandRunError as exc:
        return CommandResult(
            command=[],
            exit_code=1,
            stdout="",
            stderr=str(exc),
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
    changed_files: list[str] | None = None,
    validation: CommandResult | None = None,
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
        "changed_files": changed_files or [],
        "validation": _validation_summary(validation),
        "model_calls": model_calls,
        "response": response,
    }

    if retry is not None:
        result["retry"] = retry
    if error is not None:
        result["error"] = error

    return result


def _validation_summary(result: CommandResult | None) -> dict | None:
    if result is None:
        return None

    return {
        "command": result.command,
        "exit_code": result.exit_code,
        "passed": result.passed,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _run_validation(config) -> CommandResult | None:
    try:
        return _run_validation_command(config)
    except CommandRunError as exc:
        typer.secho(f"Validation could not start: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _run_validation_command(config) -> CommandResult | None:
    if not config.validation_command:
        return None

    return run_command(
        config.validation_command,
        cwd=Path.cwd(),
        timeout_seconds=config.validation_timeout_seconds,
    )


def _retry_after_validation_failure(
    task: str,
    changed_files: list[str],
    validation_result: CommandResult | None,
    config,
) -> None:
    if validation_result is None:
        return

    typer.echo("")
    typer.echo("Attempting one validation-fix retry.")
    retry_prompt = _build_validation_retry_prompt(
        task, changed_files, validation_result
    )
    response = _complete_with_config(FIX_VALIDATION_SYSTEM_PROMPT, retry_prompt, config)

    try:
        diff_text = extract_unified_diff(response.content)
        patch_result = apply_unified_diff(Path.cwd(), diff_text)
    except DiffExtractionError as exc:
        typer.secho(
            f"Could not find a retry patch: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        typer.echo(response.content)
        raise typer.Exit(code=1) from exc
    except PatchApplyError as exc:
        typer.secho(f"Retry patch failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Applied retry patch.")
    typer.echo("Changed files:")
    for changed_file in patch_result.changed_files:
        typer.echo(f"- {changed_file}")

    retry_validation_result = _run_validation(config)
    _print_validation_result(retry_validation_result, exit_on_failure=True)


def _build_validation_retry_prompt(
    task: str,
    changed_files: list[str],
    validation_result: CommandResult,
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
    result: CommandResult | None,
    exit_on_failure: bool = True,
) -> bool:
    if result is None:
        typer.echo("")
        typer.echo("Validation skipped: AGENT_ZERO_VALIDATION_COMMAND is not set.")
        return True

    typer.echo("")
    typer.echo(f"Validation command: {' '.join(result.command)}")
    if result.passed:
        typer.echo("Validation passed.")
        return True

    if result.timed_out:
        typer.echo("Validation timed out.")
    else:
        typer.echo(f"Validation failed with exit code {result.exit_code}.")

    if result.stdout:
        typer.echo("")
        typer.echo("Validation stdout:")
        typer.echo(result.stdout)
    if result.stderr:
        typer.echo("")
        typer.echo("Validation stderr:")
        typer.echo(result.stderr)

    if exit_on_failure:
        raise typer.Exit(code=1)
    return False
