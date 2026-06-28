# Agent Zero Finish Line

This is the short TODO list for finishing Agent Zero as a prototype before
starting Agent Hub.

The goal is not to build the full Agent Hub here. The goal is to prove the
small harness mechanics that Agent Hub will depend on.

## Finish Line Principle

Agent Zero should answer this question:

> Can we make a coding-agent run understandable, measurable, safe to inspect,
> and capable of learning only from approved signals?

If a feature does not help answer that, leave it for Agent Hub.

## Must Complete

| # | Task | Status | Why It Matters For Agent Hub |
| --- | --- | --- | --- |
| 1 | Request classifier with `read`, `plan`, `write` action types | Done | First routing decision for the Architect Conductor. |
| 2 | Clarification guard for vague write requests | Done | Prevents unsafe action and token waste. |
| 3 | Patch reliability guardrails, including stale hunk relocation | Done | Makes local patching safer and less brittle. |
| 4 | `--trace-json` for `ask` and `plan` | Done | Proves run traces can be machine-readable. |
| 5 | `--trace-json` for `code` | Done | Exposes patch, retry, validation, changed files, and main failure state. |
| 6 | Structured tool call records | Partial | Ask, plan, and code traces include major timed tool calls; deeper retrieval substeps are pending. |
| 7 | Eval suite command | Done | Lets us run regression checks before changing retrieval, memory, or prompts. |
| 8 | Cost budget guardrail | Done | Stops ask, plan, and code runs before model calls when prompt cost exceeds `--max-cost`. |
| 9 | Better relevance filtering | Done | Content-search hits now need meaningful query-term matches, reducing weak context. |
| 10 | Memory approval polish | Done | Validated lessons stay candidates until review, approval, or feedback confirms them. |

## Prototype Only

These are worth sketching or lightly prototyping, but not fully building in
Agent Zero:

| Area | Agent Zero Scope | Agent Hub Scope |
| --- | --- | --- |
| Self-learning | Candidate memory, feedback, approval, rejection | Multi-user/team memory workflow |
| Persistent vectorless memory | SQLite curated lessons and repo index | Shared memory service with review and permissions |
| Vector memory | Design notes or fake deterministic tests only | Real vector store, embeddings, hybrid scoring |
| Hybrid retrieval | Improve current lexical + index + memory ranking | Full vectorless + graph + semantic + MCP retrieval |
| Human approval | Clarification and memory approval patterns | Full human gates for workflows and artifacts |

## Do Not Build In Agent Zero

- Multi-agent orchestration.
- LangGraph workflows.
- Jira, Bitbucket, Teams, Confluence, Allure, or Figma integrations.
- Mission Control UI.
- Scheduled autonomous jobs.
- Production vector database integration.
- Enterprise permissions model.
- Full dashboard or searchable run history.

These belong in Agent Hub.

## Recommended Order

1. Run the testing plan end to end.
2. Stop Agent Zero and start Agent Hub.

## Exit Criteria

Agent Zero is ready to pause when:

- A read run is traceable.
- A code run is traceable.
- Tool calls are visible as structured records.
- A small eval suite can measure regressions.
- Cost limits can stop expensive runs before model calls.
- Memory does not promote failed or unclear runs automatically.
- Validated memory requires approval or feedback before it boosts retrieval.
- Context selection is explainable and avoids obvious noise.
