from pathlib import Path

import typer

from agent_zero.config import ConfigError, load_config
from agent_zero.context import build_repository_context
from agent_zero.diff_parser import DiffExtractionError, extract_unified_diff
from agent_zero.model_client import ModelClientError, create_model_client
from agent_zero.tools.command_tool import CommandRunError, CommandResult, run_command
from agent_zero.tools.patch_tool import PatchApplyError, apply_unified_diff

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


def _print_model_response(response) -> None:
    typer.echo(response.content)

    if response.total_tokens is not None:
        typer.echo("")
        typer.echo(
            "Tokens: "
            f"input={response.input_tokens}, "
            f"output={response.output_tokens}, "
            f"total={response.total_tokens}"
        )


def _complete_or_exit(
    system_prompt: str,
    user_prompt: str,
    env_file: Path | None,
):
    config = _load_config_or_exit(env_file)
    return _complete_with_config(system_prompt, user_prompt, config)


def _complete_with_config(system_prompt: str, user_prompt: str, config):
    client = create_model_client(config)

    try:
        return client.complete(system_prompt, user_prompt)
    except ModelClientError as exc:
        typer.secho(f"Model call failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


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
    response = _complete_or_exit(ASK_SYSTEM_PROMPT, user_prompt, env_file)
    _print_model_response(response)


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
    response = _complete_or_exit(PLAN_SYSTEM_PROMPT, user_prompt, env_file)
    _print_model_response(response)


@app.command()
def code(
    task: str = typer.Argument(..., help="Change request to implement."),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        help="Optional path to a .env file.",
    ),
) -> None:
    """Apply a focused code change and validate it."""
    config = _load_config_or_exit(env_file)
    user_prompt = _build_user_prompt("Change request", task)
    response = _complete_with_config(CODE_SYSTEM_PROMPT, user_prompt, config)

    try:
        diff_text = extract_unified_diff(response.content)
        patch_result = apply_unified_diff(Path.cwd(), diff_text)
    except DiffExtractionError as exc:
        typer.secho(f"Could not find a patch: {exc}", fg=typer.colors.RED, err=True)
        typer.echo(response.content)
        raise typer.Exit(code=1) from exc
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

    if response.total_tokens is not None:
        typer.echo("")
        typer.echo(
            "Tokens: "
            f"input={response.input_tokens}, "
            f"output={response.output_tokens}, "
            f"total={response.total_tokens}"
        )


def _run_validation(config) -> CommandResult | None:
    if not config.validation_command:
        return None

    try:
        return run_command(
            config.validation_command,
            cwd=Path.cwd(),
            timeout_seconds=config.validation_timeout_seconds,
        )
    except CommandRunError as exc:
        typer.secho(f"Validation could not start: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


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
