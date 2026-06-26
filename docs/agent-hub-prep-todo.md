# Agent Hub Prep TODO

This checklist captures the Agent Zero improvements worth trying before we move
to a bigger Agent Hub.

The goal is to prove small agent primitives here first, while the codebase is
still simple and easy to reason about.

## Why Do This In Agent Zero First?

Agent Hub will need routing, traces, tool records, memory hygiene, eval suites,
cost controls, and approval gates.

Agent Zero is the safer lab for these ideas because:

- the modes are small
- the repository is understandable
- failures are easy to inspect
- tests run quickly
- behavior can be explained in docs

## Priority 1: Task Classifier

Goal: identify whether the request is read-only, planning-only, or write-capable
before a future hub routes it to an agent.

Build:

- [x] Add `python -m agent_zero classify "..."`.
- [x] Classify into top-level action types: `read`, `plan`, or `write`.
- [x] Report the recommended Agent Zero mode: `ask`, `plan`, `code`, or
      `memory`.
- [x] Add subcategory, write intent, specificity, clarification need, missing
      information, confidence, and reason.
- [x] Keep the first version deterministic/rule-based.
- [x] Add JSON output for future automation.
- [ ] Later allow model-assisted classification behind a flag.

Example:

```bash
python -m agent_zero classify "Explain Bedrock gateway"
python -m agent_zero classify "Add validation for Bedrock timeout"
python -m agent_zero classify "it worked"
```

Top-level meaning:

| Action type | Meaning | Recommended mode |
| --- | --- |
| `read` | read-only answer or explanation | `ask` |
| `plan` | read-only planning before action | `plan` |
| `write` | repository change, memory update, or possible write | `code` or `memory` |

Example output:

```text
Action type: write
Recommended mode: code
Subcategory: documentation_edit
Write intent: explicit
Specificity: low
Requires clarification: True
Missing information:
- exact documentation text or topic
Confidence: medium
Reason: The request has edit intent but is missing details needed to act safely.
```

Why Agent Hub needs it:

- Hub will route work to different agents.
- Bad routing wastes tokens and can perform the wrong action.
- Low-specificity write requests should trigger clarification instead of action.

Tests:

- [x] Ask-like prompts are `read` and recommend `ask`.
- [x] Edit prompts are `write` and recommend `code`.
- [x] Planning prompts are `plan` and recommend `plan`.
- [x] Feedback phrases are `write` and recommend `memory`.
- [x] Vague write prompts require clarification.

## Priority 2: Clarification Detection

Goal: avoid acting when the task is underspecified.

Build:

- [x] Detect vague code requests like "Add a short README note".
- [x] Return `clarification_needed` instead of treating it as `no_changes`.
- [x] Print what information is missing.
- [x] Record this as low-risk memory, not a useful file signal.

Example:

```bash
python -m agent_zero code "Add a short README note" --dry-run --trace
```

Desired outcome:

```text
Clarification needed:
- exact documentation text or topic
Recommended mode: code
Subcategory: documentation_edit
No model call made.
```

Why Agent Hub needs it:

- Hub agents should ask before doing risky or ambiguous work.
- This is the foundation for safe human-in-the-loop behavior.

Tests:

- [x] Vague README note request asks for clarification.
- [x] Specific README note request still produces a patch.
- [x] Clarification-needed runs do not validate or apply patches.

## Priority 3: Machine-Readable Trace

Goal: make agent execution inspectable by other software.

Build:

- [x] Add `--trace-json` to `ask`, `plan`, and `code`.
- [ ] Add `--trace-json` to `eval`.
- [x] Include mode, task, provider, model, selected files, skipped files,
      context budget, model calls, usage, cost, validation, memory status, and
      final status for ask, plan, and code.
- [ ] Save optional trace files under `.agent-zero/traces/`.

Example:

```bash
python -m agent_zero ask "Explain Bedrock gateway" --trace-json
python -m agent_zero code "Add one sentence to README.md saying Agent Zero exposes code trace JSON" --dry-run --trace-json
```

Why Agent Hub needs it:

- Hub needs run records.
- UI/dashboard can render traces.
- Evals can compare behavior automatically.

Tests:

- [x] JSON trace is valid JSON.
- [x] Ask trace includes selected files and usage.
- [x] Plan trace includes selected files and usage.
- [x] Code trace includes patch/validation status.
- [ ] Failed runs include error status.

## Priority 4: Tool Call Records

Goal: record each internal action as a tool call.

Build:

- [ ] Define a `ToolCallRecord`.
- [ ] Record file listing, text search, index load, memory load, context build,
      model call, patch apply, and validation command.
- [ ] Include status, duration, short input summary, short output summary, and
      error message when present.
- [ ] Add tool call records to trace JSON.

Why Agent Hub needs it:

- Multi-agent systems need auditability.
- Tool outputs must be inspectable and truncatable.
- Tool records become the basis for replay/debugging.

Tests:

- [ ] Ask run records retrieval and model tools.
- [ ] Code dry-run records retrieval and model tools but not patch apply.
- [ ] Code apply records patch and validation tools.
- [ ] Tool output summaries are bounded.

## Priority 5: Eval Suite Command

Goal: run a group of evals as a regression suite.

Build:

- [ ] Add `python -m agent_zero eval-suite evals/suites/core.json`.
- [ ] Define suite JSON format.
- [ ] Run each eval and collect result paths.
- [ ] Print pass rate, failed eval names, total tokens, and total cost.
- [ ] Save suite result JSON.

Example suite:

```json
{
  "name": "core",
  "evals": [
    "evals/ask-project.json",
    {
      "mode": "ask",
      "task": "Explain Bedrock gateway",
      "expected_terms": ["BedrockGatewayClient", "polling"],
      "forbidden_terms": ["AWS SDK"]
    }
  ]
}
```

Why Agent Hub needs it:

- Every new agent or memory change needs regression checks.
- Without suites, quality becomes vibes again.

Tests:

- [ ] Suite parser accepts file evals and inline evals.
- [ ] Suite result aggregates scores.
- [ ] Failed eval exits non-zero unless `--allow-failures` is set.
- [ ] Report includes total cost.

## Priority 6: Cost Budget Guardrail

Goal: stop or warn before spending too much.

Build:

- [ ] Add `--max-estimated-cost`.
- [ ] Add `--max-context-tokens`.
- [ ] Add config defaults in `.env`.
- [ ] Warn when context selection exceeds budget.
- [ ] For risky over-budget runs, require explicit `--yes`.

Example:

```bash
python -m agent_zero ask "Explain the whole project" --max-estimated-cost 0.01
```

Why Agent Hub needs it:

- Hub may run multiple agents and model calls.
- Budget controls need to exist before orchestration expands.

Tests:

- [ ] Below-budget run proceeds.
- [ ] Above-budget run stops with clear message.
- [ ] `--yes` allows explicit over-budget run.

## Priority 7: Human Approval Gates

Goal: separate draft actions from committed actions.

Build:

- [ ] Add a generic approval model for dangerous actions.
- [ ] Require approval before code apply when configured.
- [ ] Keep dry-run as default for risky modes if configured.
- [ ] Later reuse this pattern for Jira comments, deployments, commits, and PRs.

Why Agent Hub needs it:

- Hub will eventually call external write tools.
- Approval gates prevent accidental side effects.

Tests:

- [ ] Dry-run never applies.
- [ ] Approval-required mode blocks patch apply.
- [ ] Approval flag allows patch apply.

## Priority 8: Memory Review Flow

Goal: prevent bad self-learning.

Build:

- [ ] Add `python -m agent_zero memory --review`.
- [ ] Show candidate memories with evidence, status, confidence, and suggested
      action.
- [ ] Add explicit promote/reject by memory id.
- [ ] Keep confirmed memory protected.

Why Agent Hub needs it:

- Self-learning systems must not silently promote bad memory.
- Review flow is the bridge between manual learning and automation.

Tests:

- [ ] Candidate memory appears in review.
- [ ] Promote changes status to confirmed.
- [ ] Reject changes status to rejected.
- [ ] Confirmed memory is protected from accidental deletion.

## Priority 9: Provider And Model Routing

Goal: prepare for multiple models and providers.

Build:

- [ ] Add provider capability metadata.
- [ ] Add model purpose config: cheap model for classification, stronger model
      for code or final answer.
- [ ] Add trace output showing why a model was chosen.

Why Agent Hub needs it:

- Different agents may need different models.
- Cost and quality should be controlled by task type.

Tests:

- [ ] Classification can use cheap model or deterministic mode.
- [ ] Code can use configured stronger model.
- [ ] Trace shows selected model purpose.

## Priority 10: Run Status Model

Goal: prepare for async agent runs.

Build:

- [ ] Define run statuses: `queued`, `running`, `waiting_for_approval`,
      `completed`, `failed`, `cancelled`.
- [ ] Store run summaries locally.
- [ ] Add `python -m agent_zero runs` to inspect recent runs.

Why Agent Hub needs it:

- Hub will likely run tasks asynchronously.
- Users need progress, status, and history.

Tests:

- [ ] Successful run records completed status.
- [ ] Failed run records failed status.
- [ ] Approval-needed run records waiting status.

## Recommended Build Order

1. Task classifier.
2. Clarification detection.
3. Machine-readable trace.
4. Tool call records.
5. Eval suite command.
6. Cost budget guardrail.
7. Memory review flow.
8. Approval gates.
9. Provider/model routing.
10. Run status model.

## First Concrete Task

Start with:

```text
Add `python -m agent_zero classify "..."` with deterministic routing,
confidence, and reason output.
```

Why this first:

- It is small.
- It does not call the model.
- It teaches routing.
- Agent Hub will depend on routing from day one.
