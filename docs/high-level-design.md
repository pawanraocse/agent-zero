# Agent Zero High-Level Design

This document describes how Agent Zero should be built and why each step exists.

The goal is not to create a production-grade coding agent immediately. The goal is to learn the moving parts of a coding agent by building each part in a small, observable way.

## Learning Goals

By the end of the first version, Agent Zero should teach:

- How a coding agent turns a user request into a sequence of actions.
- How to collect useful repository context without reading everything.
- How tool calling works when tools are plain Python functions.
- How planning improves code edits.
- How patches can be applied safely.
- How validation feedback changes the next model step.
- How token usage and model choice affect behavior.

## System Overview

Agent Zero is a CLI application with three modes:

- `ask`: answer questions about a repository without editing files.
- `plan`: inspect the repository and produce an implementation plan.
- `code`: inspect, plan, edit, validate, and summarize.

At a high level, each mode runs the same core loop with different permissions.

```text
User task
  |
  v
CLI command
  |
  v
Load config and model client
  |
  v
Build system prompt for selected mode
  |
  v
Collect repository context
  |
  v
Call model
  |
  v
Run allowed tools
  |
  v
Return answer, plan, or patch summary
```

## Core Components

### CLI

The CLI is the entry point for the project.

Responsibilities:

- Parse commands and arguments.
- Select the mode: `ask`, `plan`, or `code`.
- Load configuration.
- Print useful output with clear formatting.
- Exit with meaningful status codes.

Initial commands:

```bash
agent-zero ask "What does this project do?"
agent-zero plan "Add a config loader"
agent-zero code "Add a config loader"
```

### Config

Configuration should come from environment variables and `.env`.

Required settings:

- `AGENT_ZERO_BASE_URL`
- `AGENT_ZERO_API_KEY`
- `AGENT_ZERO_MODEL`

Later settings:

- default max tokens
- temperature
- validation command
- allowed shell commands
- ignored paths

### Model Client

The model client should hide the details of the configured provider.

Responsibilities:

- Create a client from config.
- Send chat messages.
- Return assistant output.
- Track token usage when available.
- Keep the rest of the app independent from one specific provider.

This should work with hosted OpenAI-compatible APIs, local OpenAI-compatible servers, and internal Bedrock gateways.

### Prompts

Prompts should be versioned and easy to inspect.

Each mode should have its own system prompt:

- `ask` prompt: answer using repository context, do not edit.
- `plan` prompt: inspect first, then produce a structured plan.
- `code` prompt: make focused edits, validate, and summarize.

Prompt design should stay simple at first. The project should prefer visible behavior over complicated prompt machinery.

### Context Builder

The context builder decides what repository information to send to the model.

First version:

- current working directory
- file tree
- selected file contents
- search results

Later versions:

- ignore large or generated files
- track token budget
- summarize large files
- include previous tool results

### Tools

Tools are normal Python functions with clear input and output.

Initial tools:

- `list_files`: show repository files.
- `read_file`: read a text file.
- `search`: search text using ripgrep or a Python fallback.
- `apply_patch`: apply a focused patch.
- `run_command`: run validation commands.

Tool calls should be observable. The user should be able to see what the agent did and why.

### Planner

The planner turns context into a structured implementation plan.

A useful plan should include:

- problem summary
- files likely to change
- implementation steps
- validation steps
- risks or unknowns
- confidence score

Planning should happen before editing. This is one of the main ideas behind Agent Zero.

### Patch Engine

The patch engine applies focused edits.

First version:

- accept a unified diff or structured patch
- apply it to files
- report success or failure

Safety rules:

- prefer small patches
- do not rewrite whole files unless necessary
- show changed files
- keep failures readable

### Validation

Validation checks whether the change worked.

First version:

- run a user-provided command
- capture stdout, stderr, and exit code
- summarize the result

Later versions:

- infer validation command from project files
- retry after simple failures
- separate test, lint, and format steps

## Mode Behavior

### Ask Mode

Purpose: answer questions about a repository.

Allowed actions:

- list files
- read files
- search text
- call model

Disallowed actions:

- edit files
- run shell commands that change state
- apply patches

Good first target:

```bash
agent-zero ask "What does this project do?"
```

### Plan Mode

Purpose: inspect the repository and propose a change.

Allowed actions:

- list files
- read files
- search text
- call model

Output should include:

- summary
- plan
- validation strategy
- risks
- confidence score

Good first target:

```bash
agent-zero plan "Add support for loading config from .env"
```

### Code Mode

Purpose: make a focused change and validate it.

Allowed actions:

- list files
- read files
- search text
- apply patches
- run validation commands
- call model

Output should include:

- files changed
- summary of edits
- validation result
- remaining risks

Good first target:

```bash
agent-zero code "Add support for loading config from .env"
```

## Build Milestones

### Milestone 0: Runnable Skeleton

Goal: make the project importable and runnable.

Steps:

1. Create the `agent_zero/` package.
2. Add `cli.py` with `ask`, `plan`, and `code` commands.
3. Add `config.py` that loads `.env`.
4. Add a minimal smoke test.
5. Confirm `pytest` runs.

Learning outcome:

- Understand project structure, CLI wiring, and configuration.

### Milestone 1: Ask Mode Without Tools

Goal: call the model from the CLI.

Steps:

1. Add `model_client.py`. Done.
2. Send a simple prompt to the configured model. Done.
3. Print the model response. Done.
4. Handle missing config cleanly. Done.

Learning outcome:

- Understand OpenAI-compatible chat APIs and local model compatibility.

### Milestone 2: Repository Context

Goal: give the model useful repo information.

Steps:

1. Add a file listing helper. Done.
2. Add a safe text file reader. Done.
3. Add search. Done.
4. Include selected context in `ask` mode. Done.
5. Rank context using query terms, search hits, path priority, and overview priors. Done.
6. Include context selection reasons in the model prompt. Done.

Learning outcome:

- Understand how context selection affects output quality and token usage.

### Milestone 3: Plan Mode

Goal: produce structured plans before edits.

Steps:

1. Add a plan prompt. Done.
2. Return a plan with implementation, validation, risks, and confidence. Done.
3. Keep output stable enough to compare across models. Done.

Learning outcome:

- Understand how planning changes model behavior.

### Milestone 4: Patch Application

Goal: let the agent make focused edits.

Steps:

1. Add an `apply_patch` tool. Done for local patch application.
2. Ask the model for small diffs. Done in `code` mode.
3. Apply patches and report changed files. Done in `code` mode.
4. Add tests for patch success and failure. Done.

Learning outcome:

- Understand why code editing needs stricter structure than normal chat.

### Milestone 5: Validation Loop

Goal: run checks after edits.

Steps:

1. Add `run_command`. Done.
2. Run a configured validation command. Done.
3. Feed failures back into the agent once. Done.
4. Summarize final status. Done.

Learning outcome:

- Understand how validation turns the agent from a text generator into a coding workflow.

### Milestone 6: Token and Cost Tracking

Goal: make token usage visible.

Steps:

1. Capture usage data from model responses.
2. Estimate cost from model config.
3. Print per-run usage.
4. Compare local and hosted model behavior.

Learning outcome:

- Understand the tradeoff between context size, model quality, and cost.

### Milestone 7: Evaluation Tasks

Goal: compare behavior across models and prompts.

Steps:

1. Create small test repositories or tasks.
2. Run the same task across models.
3. Record success, failures, token usage, and validation result.

Learning outcome:

- Understand where smaller open source models work well and where they struggle.

## Suggested Implementation Order

Build in this order:

1. CLI skeleton.
2. Config loader.
3. Model client.
4. Ask mode.
5. File tools.
6. Context builder.
7. Plan mode.
8. Patch tool.
9. Code mode.
10. Validation.
11. Token tracking.
12. Evaluations.

Each step should leave the project runnable.

## Design Rules

- Read before writing.
- Keep tool input and output simple.
- Make every tool call visible.
- Prefer explicit code over framework magic.
- Avoid adding architecture before it solves a real problem.
- Keep changes small enough to explain in a blog post.
- Treat validation failures as useful feedback.

## First Task To Build

Start with Milestone 0.

The first concrete task is:

```text
Create the Python package, add a Typer CLI with ask/plan/code commands, load .env config, and add smoke tests.
```

That gives the project a spine. After that, each milestone can be added without rethinking the whole structure.
