# Operational Signals Analyst

The **Operational Signals Analyst** is a fork-specific, optional fifth analyst
with the stable key `operational`. It complements the upstream-derived market,
sentiment, news, and fundamentals analysts by examining operating disclosures
that are often not captured by financial-statement ratios.

The analyst is research infrastructure. Its output is not financial advice and
does not assert that an operational signal will lead to a profitable trade.

## What it covers

The analyst considers backlog, bookings, book-to-bill, remaining performance
obligations, demand visibility, capacity utilization and additions, related
capital spending, customer/supplier/segment/geographic concentration,
production and supply constraints, lead times, inventory commentary,
cancellations or delays, contract duration, customer wins/losses, and demand or
capacity guidance.

It does not force every topic onto every company. Each finding uses one of:

- `Reported`
- `Derivable from reported figures`
- `Qualitative commentary`
- `Not disclosed`
- `Not applicable`

## Enable it

Programmatic selection preserves the existing default unless `operational` is
explicitly added:

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
graph = TradingAgentsGraph(
    selected_analysts=["market", "social", "news", "fundamentals", "operational"],
    config=config,
)
```

The interactive CLI lists **Operational Signals Analyst** as a fifth checkbox
for stock analyses. It is hidden for crypto because the implemented evidence
contract is company-disclosure-specific.

## Evidence providers

Operational retrieval follows the existing `route_to_vendor()` abstraction in
`tradingagents/dataflows/interface.py`.

### SEC provider

The default operational vendor is `sec`. Set a descriptive SEC User-Agent
before a live run:

```bash
export TRADINGAGENTS_SEC_USER_AGENT="Your Name your-email@example.com"
```

`operational_max_filings` defaults to `4` and can be overridden with
`TRADINGAGENTS_OPERATIONAL_MAX_FILINGS`. `operational_fixture_path` defaults to
`None`; `TRADINGAGENTS_OPERATIONAL_FIXTURE_PATH` may point offline mode at a
compatible local synthetic dataset.

The provider resolves the ticker through SEC company metadata, selects recent
10-K, 10-Q, and 8-K filings whose filing date is on or before the analysis date,
and extracts short keyword-matched operational passages. It does not infer a
number or claim that a filing does not state.

Only the recent filing index is currently inspected; older filing-index pages
are a known limitation. Filing retrieval failures are reported and do not cause
the analyst to fabricate a substitute source.

### Synthetic fixture provider

`TRADINGAGENTS_OFFLINE_MODE=true` changes only the operational provider to the
committed synthetic fixture dataset. This mode is for tests, demos, and
evaluation; its reports are explicitly marked synthetic.

```bash
python -m evaluations.run \
  --config evaluations/configs/smoke.yaml \
  --case synthetic_backlog
```

## Temporal integrity

`prepare_evidence()` and `temporal_validity()` in
`tradingagents/evidence/validation.py` recompute every evidence record's
date-validity flag. A filing/publication date after the requested date is
rejected. When both dates are unknown, the record is unusable while strict
temporal grounding is enabled.

This is intentionally stricter than filtering by reporting period. A fiscal
period end does not prove that investors could access the document on that day.
A live page retrieved today is therefore not accepted as historical evidence
unless its original public date is verifiable.

Strict mode is the default for evidence-aware analysis and can be configured
with:

```bash
TRADINGAGENTS_STRICT_TEMPORAL_GROUNDING=true
```

## Structured output and citations

`OperationalAssessment` contains the LLM-produced synthesis.
`OperationalSignalsOutput` combines that assessment with provider-owned
`EvidenceRecord` objects, normalized `ClaimRecord` objects, and a
`CitationValidationResult`. The LLM receives only deterministic evidence IDs;
it does not create or edit source records.

Reports use inline IDs such as `[EVID-2F4A...]` and end with a deduplicated
Sources section. Invalid, missing, future-dated, wrong-company, or conflicting
support is labeled in the report and retained in machine-readable validation
state.

The following optional graph-state keys are added:

- `operational_report`
- `operational_analysis`
- `operational_evidence`
- `operational_claims`
- `citation_validation`

Legacy states and reports that do not contain these keys continue to render.

## Failure behavior

When retrieval returns no usable evidence, the analyst does not call an LLM to
fill the gap. It produces a schema-valid low-confidence `Hold`-scale assessment
whose signal is explicitly unavailable, lists missing dimensions, and states
that no directional conclusion is supportable.

If a provider lacks structured output, a plain-model response may be retained
only as the overall conclusion. Individual factual findings from that fallback
are not accepted, because they would lack schema-enforced citation IDs.
