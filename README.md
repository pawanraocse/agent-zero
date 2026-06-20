# Agent Zero

Agent Zero is a small coding agent built from scratch to understand how agentic coding systems work under the hood.

This is not meant to compete with Claude Code, Codex, Cursor, or other full-featured coding agents. The point is to learn the basic loop by building it directly:

1. Inspect the codebase.
2. Decide which context matters.
3. Call simple tools.
4. Plan a focused change.
5. Apply a patch.
6. Run validation.
7. Explain what happened.

No MCP support. No plugin system. No hidden framework magic. Just a minimal harness that makes the agent loop visible.

## Why This Exists

Agent Zero is a pet project for learning how to build a coding agent from first principles.

The main goals are:

- Understand how coding agents generate useful code instead of just text.
- Learn how planning, tool calling, context selection, patching, and validation fit together.
- Keep token usage visible so cost and context tradeoffs are easier to reason about.
- Support OpenAI-compatible APIs and local model servers.
- Experiment with open source models on small and medium-sized codebases.
- Keep the architecture simple enough to explain in a blog series.

## What Makes It Different

Agent Zero is intentionally small and explicit.

- It analyzes before it writes. The agent should inspect the existing codebase, identify patterns, and understand the likely blast radius before suggesting changes.
- Planning is a first-class step. `plan` mode should produce an implementation plan, validation plan, testing notes, and a confidence score.
- It is portable. The same harness should work with hosted OpenAI-compatible APIs or local models such as Qwen through a compatible server.
- It favors observable behavior. Tool calls, selected context, patches, and validation results should be easy to inspect.

## Modes

The project is designed around three core modes:

- `ask`: repo-aware Q&A without editing files.
- `plan`: analysis, implementation planning, validation planning, and confidence scoring.
- `code`: focused code changes with patching and validation.

## Planned Features

- CLI entry point for running agent tasks.
- File read and directory inspection tools.
- Fast text search over the target repository.
- Patch application for focused edits.
- Shell command execution for validation.
- Prompt templates for each mode.
- Token and cost tracking.
- Small evaluation tasks to compare model behavior.

## Non-Goals

Agent Zero deliberately avoids some features, at least for the first version:

- MCP integration.
- Plugin marketplaces.
- Multi-agent orchestration.
- Remote workspace management.
- Large IDE-style UI.
- Complex memory systems.

The first version should stay boring, inspectable, and easy to modify.

## Suggested Project Structure

```text
agent-zero/
|-- agent_zero/
|   |-- __init__.py
|   |-- cli.py
|   |-- agent.py
|   |-- config.py
|   |-- prompts.py
|   |-- model_client.py
|   |-- context.py
|   |-- planner.py
|   |-- tools/
|   |   |-- __init__.py
|   |   |-- read_file.py
|   |   |-- search.py
|   |   |-- apply_patch.py
|   |   `-- run_command.py
|   `-- validation.py
|-- tests/
|-- .env.example
|-- requirements.txt
`-- README.md
```

## Setup

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a local environment file:

```bash
cp .env.example .env
```

For an OpenAI-compatible API, use values like:

```bash
AGENT_ZERO_BASE_URL=https://api.openai.com/v1
AGENT_ZERO_API_KEY=your_api_key_here
AGENT_ZERO_MODEL=gpt-4.1-mini
```

For a local model server, point `AGENT_ZERO_BASE_URL` at the local OpenAI-compatible endpoint and set `AGENT_ZERO_MODEL` to the local model name.

Example:

```bash
AGENT_ZERO_BASE_URL=http://localhost:1234/v1
AGENT_ZERO_API_KEY=not-needed
AGENT_ZERO_MODEL=qwen2.5-coder
```

For an internal Bedrock gateway, use:

```bash
AGENT_ZERO_PROVIDER=bedrock
AGENT_ZERO_MODEL=anthropic.claude-haiku-4-5-20251001-v1:0
AGENT_ZERO_BEDROCK_URL=https://mqa2x0ppb5.execute-api.us-east-1.amazonaws.com/dev/ai/v1/bedrock
AGENT_ZERO_BEDROCK_AUTH_HEADER=x-api-key: your_key_here
AGENT_ZERO_BEDROCK_TENANT_ID=11221122
AGENT_ZERO_TOP_P=0.2
AGENT_ZERO_MAX_TOKENS=4096
AGENT_ZERO_BEDROCK_POLL_INTERVAL_SECONDS=1
AGENT_ZERO_BEDROCK_TIMEOUT_SECONDS=120
```

`AGENT_ZERO_BEDROCK_AUTH_HEADER` should contain the full header name and value in `Header-Name: value` format. Do not commit real API keys or gateway URLs.

The Bedrock gateway is asynchronous. Agent Zero submits a `POST` request, extracts the returned request id, then polls:

```text
GET {AGENT_ZERO_BEDROCK_URL}/{request_id}?tenantId={AGENT_ZERO_BEDROCK_TENANT_ID}
```

until the gateway returns completed content or the timeout is reached.

Run the CLI from source:

```bash
python -m agent_zero ask "What does this project do?"
```

If you want the `agent-zero` console command, install the project in editable mode:

```bash
pip install -e .
```

## First Milestone

The first useful version should be able to:

1. Accept a user task from the CLI.
2. Inspect a small repository using file and search tools.
3. Produce a short implementation plan.
4. Apply a patch.
5. Run a validation command.
6. Summarize what changed and whether validation passed.

For the detailed build plan, see [Agent Zero High-Level Design](docs/high-level-design.md).

## Current Status

Milestone 0 is complete:

- Python package skeleton.
- Typer CLI with `ask`, `plan`, and `code` commands.
- `.env` based configuration loader.
- Smoke tests for the CLI and config loader.

Milestone 1 is in place:

- OpenAI-compatible model client.
- Internal Bedrock gateway model client.
- `ask` sends the user question to the configured model.
- Token usage is printed when the API returns usage data.

Milestone 2 is in place:

- Read-only repository file listing.
- Safe text file reads that ignore secrets, caches, virtualenvs, and IDE files.
- Simple text search over repository files.
- Query-aware context selection for overview, implementation, config, tests, and docs questions.
- `ask` sends selected repository context to the configured model.

The CLI does not edit files yet. Planning comes next in Milestone 3.

## Design Principles

- Read before writing.
- Prefer small patches over full-file rewrites.
- Make every tool call observable.
- Keep prompts versioned and easy to inspect.
- Treat validation failures as useful feedback.
- Prefer simple control flow over clever abstractions.
- Optimize for learning before optimizing for scale.

## Development

Run tests:

```bash
pytest
```

Format code:

```bash
ruff format .
```

Lint code:

```bash
ruff check .
```

## Blog Series Notes

This repository is also intended to support a blog series about building a coding agent from scratch.

Possible posts:

1. Why build a coding agent from scratch?
2. Designing the basic agent loop.
3. Building tool calls without a plugin framework.
4. Planning before editing.
5. Applying patches safely.
6. Running validation and learning from failures.
7. Making local and open source models work better on code.
