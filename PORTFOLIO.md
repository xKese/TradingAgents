# Portfolio Case Study: Extending TradingAgents

## Upstream project and attribution

This repository is my fork of
[`TauricResearch/TradingAgents`](https://github.com/TauricResearch/TradingAgents),
the multi-agent financial research framework by Yijia Xiao, Edward Sun, Di Luo,
and Wei Wang. I did not author the original framework. I preserved its
Apache-2.0 license, attribution, project history, agents, providers, CLI,
checkpointing, and decision-memory design.

## Why I extended it

I chose a mature multi-agent codebase because it creates a realistic extension
problem: new analysis must fit an existing graph, state model, provider router,
CLI, persistence system, tests, and backward-compatibility contract.

My pre-implementation audit found four engineering gaps worth addressing:

1. financial statements and market/news analysis did not provide a dedicated
   view of backlog, capacity, concentration, and supply-chain signals;
2. prompts asked for evidence, but reports had no shared claim-to-citation
   contract or deterministic validator;
3. pytest covered implementation behavior, but there was no committed
   research-quality evaluation dataset and report generator;
4. callback counters existed, but there was no durable, redacted run trace.

The detailed baseline is in `docs/extension-audit.md`.

## What I added

- An optional `operational` analyst registered through the existing
  metadata-driven LangGraph setup, ToolNode loop, state propagation, CLI, report
  writer, and checkpoint signature.
- A source-owned `EvidenceRecord` and `ClaimRecord` model with stable IDs,
  duplicate consolidation, point-in-time validation, URL/unit/ticker/company
  checks, conflict detection, unsupported-claim labels, and Sources rendering.
- SEC filing retrieval through the existing vendor router, plus a fully
  synthetic offline provider for deterministic tests and demos.
- A reproducible `evaluations` package that writes JSON, CSV, and Markdown and
  compares the legacy analyst set with operational/citation variants.
- Disabled, local JSONL, and optional LangSmith tracing modes with redaction,
  hashing, rotation, provider-metadata handling, and explicit cost-estimation
  limits.
- Focused tests and documentation that distinguish upstream components from my
  fork-specific work.

## Architecture decisions and tradeoffs

I kept the operational analyst optional so the upstream default graph is
unchanged. Evidence is retrieved deterministically before model synthesis; this
limits the model's freedom, but it prevents fabricated URLs and makes temporal
rules testable. Strict date validation rejects undated material, which can lower
coverage but is safer than historical leakage.

The SEC parser uses short keyword-matched filing passages rather than a second
LLM extraction pass. This is inexpensive and auditable, but it does not yet
understand tables, exhibits, amended filing indexes, or semantic paraphrases as
well as a dedicated filing pipeline would.

Local tracing uses append-only JSONL instead of requiring an observability
backend. That improves portability and offline debugging, while LangSmith
remains available through a standard optional integration.

## Tests and evaluation methodology

The extension follows the repository's pytest and Ruff conventions. Unit tests
cover factory output, graph registration, state handoff, CLI selection,
evidence IDs, validation rules, temporal boundaries, no-op/local/external
tracing behavior, fixture metrics, report generation, and offline execution.

The evaluation dataset is synthetic and intentionally adversarial. It contains
look-ahead, wrong-company, missing-URL, conflict, provider-failure, and duplicate
cases. Deterministic metrics are kept separate from optional evaluator-model
judgments. No benchmark is described as a return or investment-performance
result.

## How this differs from an unmodified deployment

An unmodified deployment runs the original analyst/research/trader/risk pipeline
against configured providers. This fork adds a new domain role and cross-cutting
evidence, evaluation, and observability contracts while preserving that default
path. The engineering contribution is therefore integration and production
hardening, not repackaging or renaming upstream work.

## Known limitations

- SEC retrieval covers the recent submissions index and keyword-matched 10-K,
  10-Q, and 8-K primary documents; international regulator support is not yet
  implemented.
- Existing analysts are report-compatible with the Sources renderer, but they
  do not yet emit the new claim/evidence schema.
- Free-text fallback cannot safely preserve detailed operational findings, so it
  emits only a conservative conclusion.
- Local callback metadata depends on what each LangChain provider exposes.
- Synthetic evaluation verifies engineering behavior, not live retrieval
  quality or financial outcomes.

## What I would build next

The next highest-value step is a point-in-time filing index and semantic section
retriever that supports SEC exhibits and equivalent international regulators,
then migrates the existing fundamentals and news analysts onto the same
claim/evidence contract.
