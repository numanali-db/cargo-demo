# AI mindset — how this demo was built

AI was used as a force multiplier across the whole build, not just inside the product. This documents the tools, prompts, and patterns used so the work is reproducible and the decisions are transparent (FE Bar "Build + AI Mindset").

## Tools

- **Claude Code** (Anthropic, in the Databricks FE `vibe` environment) — primary build agent: scaffolding, the Lakeflow pipeline, the MLflow pyfunc agent, the FastAPI app, the Lakebase provisioning, SQL, and all docs.
- **Databricks skills / MCP** invoked through Claude Code: `databricks-lakebase`, `databricks-apps`, `databricks-query`, workspace auth, and the FE Bar validation skill.
- **Foundation Model API** inside the product — `databricks-claude-sonnet-4-6` drafts the quote rationale in the agent's final step.

## Patterns that worked

- **AI as the integrator, human as the architect.** The six-stage architecture, the "agent-not-a-model" decision, and the Lakebase-vs-lakehouse split were human calls; Claude Code executed and wired them together. See [decisions & trade-offs](../README.md#decisions--trade-offs).
- **Evidence-first.** Every stage was *run* and its real output captured as text (`docs/execution-evidence.md`) rather than asserted — the same discipline the FE Bar evaluator applies.
- **Parameterise, don't hardcode.** All workspace-specific values live in `.env` and render into `app.yaml`/SQL via `scripts/render.sh`; the provisioning script (`scripts/provision_lakebase.py`) takes required env vars, no embedded identifiers. This kept the repo publishable and demo-portable.
- **Verify against the live platform.** Lakebase reads/writes, the agent endpoint, Genie, and the app KPIs were each checked end-to-end on a real workspace before being called done.

## Representative prompts

- *"Add a real Lakebase Postgres backend for the operational RFQ inbox + quotes; keep analytics on the warehouse; app-side OAuth via the app service principal."*
- *"Anchor the analytics time-windows to the latest date in the data so the KPI tiles populate whenever the demo is shown."*
- *"Validate this build against the FE Bar rubric and close the gaps — execution evidence, business deck, trade-offs, and genericise the customer name."*

## What AI did *not* decide

The industry framing, the value narrative, the KPI choices, and the architectural trade-offs are the author's — AI accelerated the build and the writing, but the judgement and the story are human.
