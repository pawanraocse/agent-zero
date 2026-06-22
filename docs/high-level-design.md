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

1. Capture usage data from model responses. Done.
2. Estimate cost from model config. Done.
3. Print per-run usage. Done.
4. Compare local and hosted model behavior.

Learning outcome:

- Understand the tradeoff between context size, model quality, and cost.

### Milestone 7: Evaluation Tasks

Goal: compare behavior across models and prompts.

Steps:

1. Create small test repositories or tasks. Started with JSON eval specs.
2. Run the same task across models. Started with the `eval` CLI command.
3. Record success, failures, token usage, and validation result. Done.

Learning outcome:

- Understand where smaller open source models work well and where they struggle.

### Milestone 8: Dry Run For Code Mode

Goal: preview generated patches before files are changed.

Steps:

1. Add a `--dry-run` option to `code`. Done.
2. Extract and print the proposed unified diff. Done.
3. Skip patch application and validation. Done.
4. Still report token usage and estimated cost. Done.

Learning outcome:

- Understand that patch generation and patch execution are two separate phases.

### Milestone 9: Patch Summaries

Goal: report deterministic patch size information.

Steps:

1. Parse unified diffs into per-file additions and deletions. Done.
2. Print patch summaries in normal `code` mode. Done.
3. Print patch summaries in `code --dry-run`. Done.
4. Store patch summaries in code eval results. Done.

Learning outcome:

- Understand why agent reports should include measurable facts, not only model
  explanations.

### Milestone 10: Narrative Repo Index

Goal: build a local memory map that improves context selection.

Steps:

1. Add an `index` command. Done.
2. Generate `.agent-zero/index.json`. Done.
3. Store file summaries, concepts, symbols, imports, and relationships. Done.
4. Use index concepts and relationships as retrieval signals. Done.

Learning outcome:

- Understand how external memory can make an agent better at finding relevant
  files without changing the model itself.

### Milestone 11: Learning Signals Memory

Goal: use compact feedback records to improve future retrieval.

Steps:

1. Append compact records to `.agent-zero/memory.jsonl`. Done.
2. Record selected files, useful files, status, success, usage, and validation
   signals. Done.
3. Keep recent records only to avoid unbounded memory growth. Done.
4. Boost files from similar successful past tasks during context selection. Done.
5. Avoid treating every read-only selected file as useful. Done.
6. Prefer implementation files over tests for non-test questions. Done.
7. Keep relationship-only files behind direct matches during selection. Done.

Learning outcome:

- Understand the difference between answer caching and self-improving retrieval
  memory.
- Understand that repository graph edges are supporting evidence, not a
  replacement for direct relevance.

### Milestone 12: Context Debug Output

Goal: expose retrieval decisions at the CLI.

Steps:

1. Add `--show-context` to `ask`. Done.
2. Add `--show-context` to `plan`. Done.
3. Print query terms, index usage, memory usage, selected files, and reasons.
   Done.

Learning outcome:

- Understand how to debug whether index and learning memory affect retrieval.

### Milestone 13: Context Budgeting

Goal: make selected context size explicit and controllable.

Steps:

1. Add a default selected-content budget. Done.
2. Add `--context-budget` to `ask`. Done.
3. Add `--context-budget` to `plan`. Done.
4. Truncate high-ranked files when they exceed the remaining budget. Done.
5. Skip lower-ranked file contents when the budget is exhausted. Done.
6. Print budget, selected content size, truncated files, and skipped files in
   context debug output. Done.

Learning outcome:

- Understand that context selection has two layers: retrieval chooses relevant
  files, and budgeting decides how much of those files reaches the model.

### Milestone 14: Agent Trace Output

Goal: expose the high-level agent loop from the CLI.

Steps:

1. Add `--trace` to `ask`. Done.
2. Add `--trace` to `plan`. Done.
3. Print config, file listing, search, index usage, memory usage, selected
   files, budget, truncation, skipped content, and model-call preparation. Done.

Learning outcome:

- Understand the difference between retrieval debugging and execution tracing:
  `--show-context` explains why files were selected, while `--trace` explains
  what the agent did in order.

### Milestone 15: Focused Context Snippets

Goal: make budgeted file content more relevant.

Steps:

1. Add a focused text-file reader. Done.
2. Find query-term matches inside selected files. Done.
3. Keep nearby lines around those matches when the file must be truncated. Done.
4. Label focused excerpts in the prompt. Done.
5. Print focused files in context debug output and trace output. Done.

Learning outcome:

- Understand that context compression should preserve the evidence most likely
  to answer the user's question.

### Milestone 16: Symbol-Aware Context Snippets

Goal: preserve code structure during context compression.

Steps:

1. Parse Python files with `ast` when focused snippets are needed. Done.
2. Detect class, function, and async function ranges. Done.
3. Prefer symbols whose name or header matches the query. Done.
4. Fall back to line-window excerpts when symbol extraction is unavailable.
   Done.

Learning outcome:

- Understand how coding agents can use language structure to send better
  context without increasing token cost.

### Milestone 17: Oversized Symbol Slicing

Goal: compress large Python classes after they have been selected.

Steps:

1. Detect when a matched class is larger than the available snippet budget.
   Done.
2. Preserve the class header and docstring. Done.
3. Rank direct child methods by query match, public API value, and constructor
   value. Done.
4. Include selected method chunks until the budget is exhausted. Done.
5. Label sliced class and method line ranges in the prompt. Done.

Learning outcome:

- Understand that context compression can happen in layers: file selection,
  symbol selection, and then method selection inside oversized symbols.

### Milestone 18: Method Body Slicing

Goal: preserve important control-flow lines inside oversized methods.

Steps:

1. Detect when a method chunk is too large for its slice budget. Done.
2. Preserve the method signature. Done.
3. Keep important windows around payloads, HTTP calls, response parsing,
   request IDs, polling, return statements, raise statements, status, content,
   tenant, model, and timeout lines. Done.
4. Prefer behavior methods such as `complete` and polling over constructor
   setup when budget is tight. Done.

Learning outcome:

- Understand that high-quality context compression can preserve behavior without
  sending every line of a long method.

### Milestone 19: Evidence Boundary

Goal: make selected files and included file contents visibly different.

Steps:

1. Track files whose contents were included in the prompt. Done.
2. Track selected files whose contents were skipped by the context budget. Done.
3. Add an evidence boundary section to the repository prompt. Done.
4. Print included content files in context debug output and trace output. Done.
5. Instruct the model not to make detailed claims from skipped files unless
   search result lines provide that detail. Done.

Learning outcome:

- Understand that retrieval has multiple evidence levels: selected file,
  search-result line, and included file content.

### Milestone 20: Code Trace Output

Goal: expose the write path from the CLI.

Steps:

1. Add `--trace` to `code`. Done.
2. Print context/pre-model trace for code mode. Done.
3. Print trace steps for model response, diff extraction, patch summary, dry run,
   patch application, validation, and retry. Done.
4. Keep normal code-mode output unchanged when `--trace` is not used. Done.

Learning outcome:

- Understand the full coding-agent loop: context, model, diff, patch,
  validation, and retry.

### Milestone 21: Documentation Target Narrowing

Goal: make retrieval respect simple documentation edit intent.

Steps:

1. Detect explicit target files from the task text. Done.
2. Boost explicit target files during scoring. Done.
3. For documentation edit tasks, select the target document before supporting
   implementation files. Done.
4. Print detected target files in context debug output. Done.

Learning outcome:

- Understand that retrieval should adapt to the kind of task: broad explanation,
  targeted edit, validation, or investigation.

### Milestone 22: Layered Validation Commands

Goal: split validation into clearer stages.

Steps:

1. Add `AGENT_ZERO_TEST_COMMAND`. Done.
2. Add `AGENT_ZERO_LINT_COMMAND`. Done.
3. Add `AGENT_ZERO_FORMAT_COMMAND`. Done.
4. Run configured validation stages in test, lint, format order. Done.
5. Stop at the first failed validation stage. Done.
6. Keep `AGENT_ZERO_VALIDATION_COMMAND` backward compatible. Done.

Learning outcome:

- Understand that validation can provide more useful feedback when tests, lint,
  and formatting are separate signals.

### Milestone 23: Empty Patch Guardrail

Goal: reject diffs that do not contain real file edits.

Steps:

1. Summarize model-generated diffs before dry-run or patch application. Done.
2. Detect summaries with zero additions and zero deletions. Done.
3. Stop `code` mode with a clear empty-patch error. Done.
4. Apply the same guardrail to code evals. Done.

Learning outcome:

- Understand that parsing a diff is not enough. The agent also needs a semantic
  sanity check that the diff would actually change the repository.

### Milestone 24: Empty Patch Retry

Goal: retry patch generation once after an empty diff.

Steps:

1. Detect an empty patch before dry-run or patch application. Done.
2. Build a retry prompt with the original task, repository context, and
   rejected empty diff. Done.
3. Call the model once more for a corrected diff. Done.
4. Continue the normal dry-run or patch-application flow when the retry patch is
   non-empty. Done.
5. Stop clearly if the retry also returns an empty patch or no patch. Done.

Learning outcome:

- Understand recovery loops. The agent can retry the failed stage with targeted
  feedback instead of repeating context discovery or silently accepting a bad
  patch.

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
13. Dry run.
14. Patch summaries.
15. Narrative repo index.
16. Learning signals memory.
17. Context debug output.
18. Context budgeting.
19. Agent trace output.
20. Focused context snippets.
21. Symbol-aware context snippets.
22. Oversized symbol slicing.
23. Method body slicing.
24. Evidence boundary.
25. Code trace output.
26. Documentation target narrowing.
27. Layered validation commands.
28. Empty patch guardrail.
29. Empty patch retry.

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
