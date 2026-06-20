from pathlib import Path

import typer

from agent_zero.config import ConfigError, load_config
from agent_zero.context import build_repository_context
from agent_zero.model_client import ModelClientError, create_model_client

app = typer.Typer(
    help="Agent Zero: a minimal coding agent built from scratch.",
    no_args_is_help=True,
)

ASK_SYSTEM_PROMPT = """You are Agent Zero, a minimal coding-agent learning project.
Answer questions using the provided repository context.
Be clear about what the context does and does not show.
Do not claim you edited files or ran validation.
When useful, mention relevant file paths from the context."""


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
    config = _load_config_or_exit(env_file)
    client = create_model_client(config)
    repository_context = build_repository_context(Path.cwd(), task)
    user_prompt = (
        f"User question:\n{task}\n\n"
        f"Repository context:\n{repository_context.to_prompt()}"
    )

    try:
        response = client.complete(ASK_SYSTEM_PROMPT, user_prompt)
    except ModelClientError as exc:
        typer.secho(f"Model call failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(response.content)

    if response.total_tokens is not None:
        typer.echo("")
        typer.echo(
            "Tokens: "
            f"input={response.input_tokens}, "
            f"output={response.output_tokens}, "
            f"total={response.total_tokens}"
        )


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
    _run_stub("plan", task, env_file)


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
    _run_stub("code", task, env_file)
