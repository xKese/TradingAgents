# PR32: DeepSeek/OpenAI-compatible multi-agent research

PR32 adds an optional provider-neutral LLM research layer over normalized
records. The legacy TradingAgents graph and deterministic workflow stay intact.

## Server-side configuration

“DeepSeek V4 Pro” is not hard-coded as a model identifier. Configure the exact
model ID supplied by the account:

```powershell
$env:TRADINGAGENTS_RESEARCH_LLM_PROVIDER = "deepseek"
$env:TRADINGAGENTS_RESEARCH_LLM_MODEL = "<exact model id from DeepSeek>"
$env:DEEPSEEK_API_KEY = "<server-side key>"
```

For compatibility with the local workstation, `Deepseek Token-TA` is accepted as a legacy server-side alias for `DEEPSEEK_API_KEY`. When either key is present and provider/model are omitted, the research adapter selects `deepseek` and `deepseek-v4-pro`.

The existing DeepSeek adapter uses the OpenAI-compatible endpoint
`https://api.deepseek.com`. `TRADINGAGENTS_RESEARCH_LLM_BASE_URL` can override
the endpoint. The cockpit receives readiness, provider, and model only; keys
are never serialized into HTML, requests, jobs, or responses.

## Orchestration and safety

Four specialists (fundamentals, market, game/news/approvals, valuation) feed a
single-round Bull/Bear debate and Research Manager synthesis. Successful stages
are `AgentOutputEnvelope` records with provider, model, prompt version,
evidence, latency, and usage when available. Out-of-context citations are
rejected; a failed role degrades explicitly while remaining roles continue.

The manager emits an `InvestmentThesis`, never a `TradeSignal`. LLM output
cannot approve sizing or bypass deterministic risk review. Select **多智能体研究**
in the cockpit; missing configuration fails cleanly before model calls.
