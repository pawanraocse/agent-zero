# Agent Zero Project TODO

This TODO is the learning roadmap for Agent Zero. The goal is not only to add
features, but to understand how a coding agent behaves under pressure:
retrieval, context budgeting, patching, validation, memory, evaluation, and
provider behavior.

Each task should answer three questions:

1. What agent behavior are we trying to understand?
2. What code change makes that behavior visible?
3. How do we test that the behavior improved?

## How To Use This Roadmap

- Pick one task at a time.
- Add or update tests before trusting the behavior.
- Run the same eval before and after the change.
- Record the effect on selected files, tokens, cost, status, and score.
- Keep the implementation small enough to explain in the README.

Useful commands:

```bash
python -m agent_zero ask "Explain Bedrock gateway" --show-context --trace
python -m agent_zero ask "Explain Bedrock gateway" --context-budget 400
python -m agent_zero eval --mode ask "Explain Bedrock gateway" --expect BedrockGatewayClient
python -m agent_zero eval-report
python -m agent_zero memory
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

For a command-by-command manual test sequence, see `docs/testing-plan.md`.

For the short finish line before Agent Hub, see
`docs/agent-zero-finish-line.md`.

For the focused set of Agent Zero improvements to try before building Agent
Hub, see `docs/agent-hub-prep-todo.md`.

## Priority 1: Stronger Eval Suites

Why this matters:

Agent changes are hard to judge by feel. We need repeatable tasks that tell us
whether retrieval, memory, patching, and validation became better or worse.

Tasks:

- [ ] Add eval specs for core ask questions.
  - Example: project overview, Bedrock gateway, memory flow, validation flow.
  - Test by running `python -m agent_zero eval evals/<file>.json`.
  - Verify expected terms and forbidden terms are scored.
- [ ] Add eval specs for plan mode.
  - Example: "Plan how to add a new provider" and "Plan a README-only change".
  - Verify the response includes relevant files, implementation steps,
    validation steps, risks, and confidence.
- [ ] Add safe code-mode eval fixtures.
  - Use tiny temporary files or fixture repos where patches are safe.
  - Verify changed files, patch summaries, validation results, and retry fields.
- [ ] Add regression evals for previous failures.
  - Empty patch returned by model.
  - Patch context mismatch.
  - Validation failure followed by retry.
  - README/documentation target narrowing.
- [x] Add an eval-suite command.
  - Proposed command: `python -m agent_zero eval-suite evals/suites/core.json`.
  - Suite file should list multiple eval specs.
  - Result should include pass rate, total tokens, total cost, and failed tasks.
  - Suite pass/fail should include deterministic score checks, not just command
    completion.
- [ ] Add eval report comparison.
  - Proposed command: `python -m agent_zero eval-report --compare latest previous`.
  - Compare score, cost, selected file count, and changed files.

Tests to add:

- Unit tests for suite parsing.
- CLI tests for suite execution.
- CLI tests for report comparison.
- Fixture tests for pass/fail score aggregation.

## Priority 2: Better Memory Learning

Why this matters:

Memory should not become a pile of stale logs. It should learn useful retrieval
signals from successful work and avoid reinforcing failed or irrelevant context.

Tasks:

- [ ] Add memory promotion rules based on eval outcomes.
  - Confirm memory only when eval score passes.
  - Reject memory when eval score fails or forbidden terms appear.
- [ ] Store why a memory item was created.
  - Include source signal: validation, eval score, explicit user feedback,
    detected feedback, or manual confirmation.
- [ ] Add memory confidence decay.
  - Lower confidence when old memory is not reused.
  - Keep confirmed memory protected unless explicitly rejected.
- [ ] Add memory conflict detection.
  - Detect when two memory items recommend different files for similar terms.
  - Show conflicts in `python -m agent_zero memory`.
- [ ] Add memory review command.
  - Proposed command: `python -m agent_zero memory --review`.
  - Show candidate items with evidence and suggested action.
- [ ] Add memory export/import.
  - Useful for learning experiments and backups.
  - Keep raw JSONL separate from curated SQLite export.

Tests to add:

- Memory item status transition tests.
- Conflict detection tests.
- Feedback plus eval-score interaction tests.
- CLI tests for review/export/import.

## Priority 3: Semantic Retrieval

Why this matters:

The current retrieval is mostly lexical: query terms, paths, text search, index
concepts, and memory boosts. That is understandable, but it misses files when
the user uses different words than the codebase.

Tasks:

- [ ] Add a local embedding interface.
  - Start with an optional provider abstraction.
  - Do not make embeddings required for basic usage.
- [ ] Store file-level embeddings in a local index.
  - Candidate stores: SQLite vector extension, Redis local vector index, or a
    simple local JSON/NumPy store for learning.
- [ ] Store chunk-level embeddings for larger files.
  - Chunk by symbols when possible.
  - Fall back to text windows for non-code files.
- [ ] Blend semantic score with existing retrieval reasons.
  - Keep the explanation visible: lexical hit, index match, memory boost,
    semantic similarity.
- [ ] Add `--retrieval-mode lexical|semantic|hybrid`.
  - Useful for comparing behavior and cost.
- [ ] Add semantic retrieval evals.
  - Same prompt, different retrieval mode, compare selected files and answer
    quality.

Tests to add:

- Deterministic fake embedding tests.
- Hybrid ranking tests.
- Context explanation tests showing semantic reasons.
- Eval comparison tests for retrieval modes.

## Priority 4: Context Compression

Why this matters:

Agents fail when they send too much context or the wrong context. We need better
ways to reduce token cost without hiding important code.

Tasks:

- [ ] Add file summaries generated during `index`.
  - Store summaries separately from raw files.
  - Include symbols, responsibilities, key config, and relationships.
- [ ] Add summary-first context mode.
  - Send summaries for lower-ranked files and full snippets for top files.
- [ ] Add query-focused summarization.
  - For a selected large file, summarize only the parts relevant to the user
    task.
- [ ] Add context budget reporting by file.
  - Show exact approximate tokens per included file.
- [ ] Add skipped-context warnings.
  - Warn when an important-looking file was selected but content was skipped.
- [ ] Add "need more context" retry.
  - If the model says context is insufficient, rerun retrieval with a larger
    budget or a narrower target.

Tests to add:

- Summary storage tests.
- Budget allocation tests.
- Show-context output tests.
- Eval tests comparing cost before and after compression.

## Priority 5: Patch Reliability

Why this matters:

Code mode depends on the model producing an applyable patch. Patch failures are
one of the clearest places where coding agents feel brittle.

Tasks:

- [x] Relocate stale hunk line numbers when exact original context still exists.
  - This handles model diffs whose line anchors are slightly outdated.
  - The patch still applies only when the original hunk lines match exactly.
- [ ] Add targeted patch anchors.
  - Include line numbers or surrounding anchors in code prompts.
  - Keep unified diff output as the model contract.
- [ ] Add patch preflight validation.
  - Check file existence and hunk context before applying.
  - Print specific mismatch diagnostics.
- [ ] Add smarter patch repair.
  - On mismatch, include the exact current target excerpt and failed hunk.
- [ ] Add multi-file patch summary improvements.
  - Show file-level risk, changed symbols, and validation impact.
- [ ] Add "no-op request" handling.
  - If the requested change already exists, ask the model to return a no-change
    marker instead of an empty diff.
- [ ] Add optional apply strategy experiments.
  - Compare strict unified diff application with safer structured edit tools.

Tests to add:

- Patch mismatch tests.
- Patch repair retry tests.
- No-op request tests.
- Multi-file diff summary tests.

## Priority 6: Validation Intelligence

Why this matters:

Validation is the agent's reality check. It should run the right tests, explain
failures clearly, and use failures as learning signals.

Tasks:

- [ ] Select validation commands based on changed files.
  - Docs-only change may skip full tests unless configured otherwise.
  - Python code change should run tests and lint.
- [ ] Add validation profiles.
  - Example: `quick`, `full`, `docs`, `python`.
- [ ] Add validation output summarization.
  - Keep raw output in eval JSON.
  - Print concise failure summary in terminal.
- [ ] Add timeout diagnostics.
  - Distinguish test failure from timeout.
- [ ] Add validation memory signals.
  - Confirm useful files only after validation passes.
  - Reject or lower confidence after repeated validation failures.

Tests to add:

- Profile selection tests.
- Timeout tests.
- Validation summary tests.
- Memory update tests after validation.

## Priority 7: Provider Behavior

Why this matters:

Agent Zero should teach how provider differences affect the agent loop:
synchronous OpenAI-compatible APIs, async Bedrock gateway, usage reporting,
timeouts, and errors.

Tasks:

- [ ] Add provider capability descriptions.
  - Example: supports usage, supports streaming, supports async polling.
- [ ] Add Bedrock gateway diagnostics.
  - Show submit ID, poll count, final status, and elapsed time in trace mode.
- [ ] Add provider retry policy.
  - Retry transient HTTP errors.
  - Avoid retrying deterministic bad requests.
- [ ] Add optional streaming for OpenAI-compatible providers.
  - Keep non-streaming as the default for simple testing.
- [ ] Add provider eval fixtures with mocked clients.
  - Test usage extraction, polling, timeout, and error formatting.

Tests to add:

- Bedrock poll trace tests.
- Timeout tests.
- Usage extraction tests.
- Provider retry tests.

## Priority 8: Tool Calling Internals

Why this matters:

The project currently has tools as Python functions owned by the harness. A
real coding agent needs a clearer tool protocol: tool schema, tool call request,
tool result, and safety boundaries.

Tasks:

- [ ] Define a minimal tool protocol.
  - Tool name, description, input schema, output schema.
- [ ] Add explicit tool call records to trace.
  - File list, search, read, patch, command.
- [ ] Add a read-only tool mode for ask/plan.
  - Prevent write tools unless mode is `code`.
- [ ] Add tool permission checks.
  - Require explicit allowlist for shell commands.
- [ ] Add tool result truncation rules.
  - Avoid flooding model context with huge command output.
- [ ] Add a tool-call eval.
  - Verify which tools were used for a given task.

Tests to add:

- Tool schema tests.
- Permission tests.
- Trace output tests.
- Output truncation tests.

## Priority 9: Better Planning Mode

Why this matters:

Plan mode should be more than prose. It should produce a useful, testable plan
that code mode or a human can follow.

Tasks:

- [ ] Add structured plan output.
  - Fields: summary, files, steps, validation, risks, confidence.
- [ ] Save plan artifacts.
  - Proposed command option: `--save-plan`.
- [ ] Add plan-to-code handoff.
  - Code mode can optionally consume a saved plan.
- [ ] Add plan quality scoring.
  - Deterministic checks for files, validation, risks, and confidence.
- [ ] Add plan evals.
  - Compare whether retrieval or memory improves plan quality.

Tests to add:

- Plan parser tests.
- Plan artifact tests.
- Plan scoring tests.
- Plan eval tests.

## Priority 10: Packaging And Developer Experience

Why this matters:

The project should be easy to install, run, debug, and teach from scratch.

Tasks:

- [ ] Add console script documentation.
  - `agent-zero ask ...` after package install.
- [ ] Add setup instructions for OpenAI and Bedrock separately.
- [ ] Add IntelliJ/PyCharm debug configuration docs.
- [ ] Add troubleshooting docs.
  - Missing dependency.
  - Wrong virtualenv.
  - Blocked network.
  - Patch mismatch.
  - Validation timeout.
- [ ] Add sample `.env` variants.
  - OpenAI-compatible.
  - Bedrock gateway.
  - Local server.
- [ ] Add release checklist.
  - Tests, lint, format, README, version, package build.

Tests to add:

- CLI entrypoint smoke tests.
- Config example validation tests.
- Packaging metadata checks.

## Priority 11: Documentation And Blog Series

Why this matters:

Agent Zero is a learning project. The documentation should teach the internal
agent loop step by step.

Tasks:

- [ ] Add a "How ask works" walkthrough.
  - File listing, search, index, memory, context budget, model call, usage.
- [ ] Add a "How code works" walkthrough.
  - Retrieval, prompt, diff extraction, patch apply, validation, memory.
- [ ] Add a "How memory works" walkthrough.
  - Raw JSONL versus SQLite, candidate versus confirmed versus rejected.
- [ ] Add a "How eval works" walkthrough.
  - Eval specs, ad-hoc evals, scoring, reports, comparison.
- [ ] Add diagrams.
  - Ask flow.
  - Code flow.
  - Memory lifecycle.
  - Eval loop.
- [ ] Add a glossary.
  - Context, retrieval, index, memory, eval, patch, validation, provider.

Tests to add:

- Documentation command examples should be manually checked.
- Optional future: doc command snippets tested through a script.

## Priority 12: Safety Boundaries

Why this matters:

Even a learning agent should make its safety model explicit. This teaches why
production agents need permissioning and audit logs.

Tasks:

- [ ] Add write-scope checks.
  - Prevent edits outside the repository root.
- [ ] Add command allowlist or confirmation mode.
  - Especially for destructive shell commands.
- [ ] Add secret redaction in traces and eval JSON.
  - Avoid writing API keys or auth headers into result files.
- [ ] Add dry-run defaults for risky operations.
  - Memory reset already follows this pattern.
- [ ] Add audit log view.
  - Show write operations, commands, memory mutations, and eval writes.

Tests to add:

- Secret redaction tests.
- Write-scope tests.
- Dangerous command rejection tests.
- Audit log tests.

## Current Recommended Next Task

Start with Priority 1:

```text
Add a small eval suite file and an eval-suite command that runs multiple evals,
then summarize pass rate, total tokens, total cost, and failures.
```

Why this is next:

- It uses the eval and eval-report foundation we already built.
- It gives us a repeatable way to test every future change.
- It makes the project easier to learn because each milestone can prove whether
  behavior improved or regressed.
