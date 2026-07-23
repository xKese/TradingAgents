<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="https://discord.com/invite/hk9PGKShPK" target="_blank"><img alt="Discord" src="https://img.shields.io/badge/Discord-TradingResearch-7289da?logo=discord&logoColor=white&color=7289da"/></a>
  <a href="./assets/wechat.png" target="_blank"><img alt="WeChat" src="https://img.shields.io/badge/WeChat-TauricResearch-brightgreen?logo=wechat&logoColor=white"/></a>
  <a href="https://x.com/TauricResearch" target="_blank"><img alt="X Follow" src="https://img.shields.io/badge/X-TauricResearch-white?logo=x&logoColor=white"/></a>
  <br>
  <a href="https://github.com/TauricResearch/" target="_blank"><img alt="Community" src="https://img.shields.io/badge/Join_GitHub_Community-TauricResearch-14C290?logo=discourse"/></a>
</div>

<div align="center">
  <!-- Keep these links. Translations will automatically update with the README. -->
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=de">Deutsch</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=es">Español</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=fr">français</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ja">日本語</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ko">한국어</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=pt">Português</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ru">Русский</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=zh">中文</a>
</div>

---

# TradingAgents: Multi-Agents LLM Financial Trading Framework

## News
- [2026-07] **Local web UI** added: run the full multi-agent pipeline from your browser with `tradingagents serve` (optional `pip install ".[web]"`). Pick ticker, provider, models, depth and analysts in a form, watch the agents stream live, and read the rendered reports and decision — localhost-only, works fully offline with Ollama or LM Studio. See [Local Web UI](#local-web-ui).
- [2026-07] **TradingAgents v0.3.1** released with correctness and stability fixes: Alpha Vantage look-ahead filtering, graph-router crash-safety, graph-shape-aware checkpoint resume, working crypto sentiment sources, a configurable LLM retry budget, Bedrock API-key auth, and Claude Sonnet 5 / Fable 5 support. See [CHANGELOG.md](CHANGELOG.md) for the full list.
- [2026-06] **TradingAgents v0.3.0** released with a verified data-access contract, an expanded provider registry (NVIDIA, Kimi, Groq, Mistral, Bedrock, and any OpenAI-compatible endpoint), FRED and Polymarket data vendors, a current-generation model catalog, and a CI gate.
- [2026-05] **TradingAgents v0.2.5** released with the grounded Sentiment Analyst, GPT-5.5 etc. model coverage, Qwen/GLM/MiniMax dual-region support, `TRADINGAGENTS_*` env-var configurability with API-key auto-detection, remote Ollama support, non-US alpha benchmarks, and ticker path-traversal hardening.
- [2026-04] **TradingAgents v0.2.4** released with structured-output agents (Research Manager, Trader, Portfolio Manager), LangGraph checkpoint resume, persistent decision log, DeepSeek/Qwen/GLM/Azure provider support, Docker, and a Windows UTF-8 encoding fix.
- [2026-03] **TradingAgents v0.2.3** released with multi-language support, GPT-5.4 family models, unified model catalog, backtesting date fidelity, and proxy support.
- [2026-03] **TradingAgents v0.2.2** released with GPT-5.4/Gemini 3.1/Claude 4.6 model coverage, five-tier rating scale, OpenAI Responses API, Anthropic effort control, and cross-platform stability.
- [2026-02] **TradingAgents v0.2.0** released with multi-provider LLM support (GPT-5.x, Gemini 3.x, Claude 4.x, Grok 4.x) and improved system architecture.
- [2026-01] **Trading-R1** [Technical Report](https://arxiv.org/abs/2509.11420) released, with [Terminal](https://github.com/TauricResearch/Trading-R1) expected to land soon.

<div align="center">
<a href="https://www.star-history.com/#TauricResearch/TradingAgents&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date" />
   <img alt="TradingAgents Star History" src="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date" style="width: 80%; height: auto;" />
 </picture>
</a>
</div>

> 🎉 **TradingAgents** officially released! We have received numerous inquiries about the work, and we would like to express our thanks for the enthusiasm in our community.
>
> So we decided to fully open-source the framework. Looking forward to building impactful projects with you!

<div align="center">

🚀 [TradingAgents](#tradingagents-framework) | ⚡ [Installation & CLI](#installation-and-cli) | 🖥️ [Local Web UI](#local-web-ui) | 🎬 [Demo](https://www.youtube.com/watch?v=90gr5lwjIho) | 📦 [Package Usage](#tradingagents-package) | 🤝 [Contributing](#contributing) | 📄 [Citation](#citation)

</div>

## TradingAgents Framework

TradingAgents is a multi-agent trading framework that mirrors the dynamics of real-world trading firms. By deploying specialized LLM-powered agents: from fundamental analysts, sentiment experts, and technical analysts, to trader, risk management team, the platform collaboratively evaluates market conditions and informs trading decisions. Moreover, these agents engage in dynamic discussions to pinpoint the optimal strategy.

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

> TradingAgents framework is designed for research purposes. Trading performance may vary based on many factors, including the chosen backbone language models, model temperature, trading periods, the quality of data, and other non-deterministic factors. [It is not intended as financial, investment, or trading advice.](https://tauric.ai/disclaimer/)

Our framework decomposes complex trading tasks into specialized roles.

### Analyst Team
- Fundamentals Analyst: Evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.
- Sentiment Analyst: Aggregates news headlines, StockTwits, and Reddit chatter into a single sentiment read to gauge short-term market mood.
- News Analyst: Monitors global news and macroeconomic indicators, interpreting the impact of events on market conditions.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI) to detect trading patterns and forecast price movements.

<p align="center">
  <img src="assets/analyst.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

### Researcher Team
- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team. Through structured debates, they balance potential gains against inherent risks.

<p align="center">
  <img src="assets/researcher.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Trader Agent
- Composes reports from the analysts and researchers to make informed trading decisions, determining the timing and magnitude of trades.

<p align="center">
  <img src="assets/trader.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Risk Management and Portfolio Manager
- Continuously evaluates portfolio risk by assessing market volatility, liquidity, and other risk factors. The risk management team evaluates and adjusts trading strategies, providing assessment reports to the Portfolio Manager for final decision.
- The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.

<p align="center">
  <img src="assets/risk.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

## Installation and CLI

### Installation

Clone TradingAgents:
```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
```

Create a virtual environment in any of your favorite environment managers:
```bash
conda create -n tradingagents python=3.12
conda activate tradingagents
```

Install the package and its dependencies:
```bash
pip install .
```

### Docker

Alternatively, run with Docker:
```bash
cp .env.example .env  # add your API keys
docker compose run --rm tradingagents
```

For local models with Ollama:
```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

### Required APIs

TradingAgents supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # Qwen — International (dashscope-intl.aliyuncs.com)
export DASHSCOPE_CN_API_KEY=...    # Qwen — China (dashscope.aliyuncs.com)
export ZHIPU_API_KEY=...           # GLM via Z.AI (international)
export ZHIPU_CN_API_KEY=...        # GLM via BigModel (China, open.bigmodel.cn)
export MINIMAX_API_KEY=...         # MiniMax — Global (api.minimax.io)
export MINIMAX_CN_API_KEY=...      # MiniMax — China (api.minimaxi.com)
export OPENROUTER_API_KEY=...      # OpenRouter
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage
```

For Azure OpenAI, copy `.env.enterprise.example` to `.env.enterprise` and fill in your credentials.

For AWS Bedrock, install the extra with `pip install ".[bedrock]"`, set `llm_provider: "bedrock"`, configure AWS credentials (environment variables, `~/.aws/credentials`, or an IAM role) and `AWS_DEFAULT_REGION`, and use a Bedrock model ID, e.g. `us.anthropic.claude-opus-4-8-v1:0`.

For local models, configure Ollama with `llm_provider: "ollama"`. The default endpoint is `http://localhost:11434/v1`; set `OLLAMA_BASE_URL` to point at a remote `ollama-serve`. Pull models with `ollama pull <name>`, and pick "Custom model ID" in the CLI for any model not listed by default.

For any other OpenAI-compatible server (vLLM, LM Studio, llama.cpp, or a custom relay), use `llm_provider: "openai_compatible"` and set the endpoint via `backend_url` (or `TRADINGAGENTS_LLM_BACKEND_URL`), e.g. `http://localhost:8000/v1` for vLLM or `http://localhost:1234/v1` for LM Studio. The model is whatever your server serves. No key is needed for local servers; set `OPENAI_COMPATIBLE_API_KEY` when the endpoint requires one. When the app runs in Docker but LM Studio runs on the host, use `http://host.docker.internal:1234/v1` instead of `localhost` (the compose file maps that name on Linux too).

#### Data vendors (Alpha Vantage)

Each data category (stock prices, technical indicators, fundamentals, news, macro) is served by a configurable vendor. The defaults use `yfinance` (keyless) for market data and `fred` for macro. To route a category through your **Alpha Vantage** key, set the key and pick the vendor — no code change needed:

```bash
export ALPHA_VANTAGE_API_KEY=...                          # your key
export TRADINGAGENTS_VENDOR_CORE_STOCK_APIS=alpha_vantage # stock prices
export TRADINGAGENTS_VENDOR_FUNDAMENTAL_DATA=alpha_vantage
export TRADINGAGENTS_VENDOR_TECHNICAL_INDICATORS=alpha_vantage
export TRADINGAGENTS_VENDOR_NEWS_DATA=alpha_vantage
```

Setting the key alone changes nothing — a category is only queried against Alpha Vantage once its `TRADINGAGENTS_VENDOR_*` var selects it. Each value is the exact vendor chain; list several for ordered fallback, e.g. `TRADINGAGENTS_VENDOR_NEWS_DATA=yfinance,alpha_vantage`. These map onto the `data_vendors` block in `tradingagents/default_config.py`, which you can still edit directly (or override per-tool via `tool_vendors`).

Alternatively, copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

### CLI Usage

Launch the interactive CLI:
```bash
tradingagents          # installed command
python -m cli.main     # alternative: run directly from source
```
You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more.

### Local Web UI

Prefer a browser over the terminal? TradingAgents ships an optional local web UI
that runs the exact same pipeline — pick a ticker, provider, models, depth and
analysts in a form, watch the agents stream their progress live, and read the
rendered reports and final decision in the page.

```bash
pip install ".[web]"   # one-time: installs FastAPI + uvicorn
tradingagents serve    # then open http://127.0.0.1:8000
```

It binds to `127.0.0.1` (localhost) only by default — it is a single-user local
tool with no auth layer. Point it elsewhere with `--host`/`--port`, and use
`--no-browser` to skip auto-opening the page.

#### With Docker

The image bundles the web extra, so you can serve the UI with Compose:

```bash
cp .env.example .env                    # add your API keys (or use Ollama)
docker compose --profile web up         # then open http://localhost:8000
```

Change the host port with `TRADINGAGENTS_WEB_PORT` (e.g. `TRADINGAGENTS_WEB_PORT=9000
docker compose --profile web up`). For a fully local run with a containerized model
backend, also start the Ollama profile and point the app at it:

```bash
# in .env: OLLAMA_BASE_URL=http://ollama:11434/v1
docker compose --profile web --profile ollama up
```

Then select the **Ollama** provider in the form. (The interactive CLI still runs the
same way: `docker compose run --rm tradingagents`.)

To use **LM Studio** (or any OpenAI-compatible server) running on your host from the
Docker web UI, select the **OpenAI-compatible** provider and set the backend URL to
`http://host.docker.internal:1234/v1` — the compose file already maps
`host.docker.internal` to the host gateway (on Linux too), so the container can reach
the server. You can also preset it in `.env` with
`TRADINGAGENTS_LLM_PROVIDER=openai_compatible` and
`TRADINGAGENTS_LLM_BACKEND_URL=http://host.docker.internal:1234/v1`.

For a fully local, zero-cost run, pick a local model backend in the provider
dropdown — no API key required:

- **Ollama** — the endpoint is prefilled (`http://localhost:11434/v1`); choose a
  pulled model or enter a custom model ID.
- **OpenAI-compatible** — for **LM Studio** (`http://localhost:1234/v1`), vLLM
  (`http://localhost:8000/v1`), or llama.cpp. The URL is prefilled with the LM
  Studio default; enter the model ID your server currently serves.

The web UI reuses the same provider/model catalog, key auto-detection, and
report writer as the CLI, so a browser run produces the same on-disk reports
under `results_dir`.

#### External factor pre-rating (`factor_context`)

`POST /api/run` accepts an optional `factor_context` object — a quantitative
pre-rating from an external screening model (e.g. the
[multi_factor](https://github.com/xKese/multi_factor) app). It is validated
(whitelisted keys, size-capped), rendered into the instrument context so
**every agent** sees it as a prior to validate or challenge, and archived in
the run's `run.json` for auditability:

```jsonc
{
  "ticker": "MBG.F", "analysis_date": "2026-07-23", // ... usual run fields ...
  "factor_context": {
    "source": "multi_factor", "as_of": "2026-07-23",
    "total_score": 78.2, "classification": "B+",
    "factor_scores": {"value": 45.1, "quality": 88.0, "growth": 71.3,
                       "momentum": 65.0, "lowvol": 59.9},
    "filter_ok": "JA", "recommendation": "BUY",
    "piotroski": 7, "altman_z": 4.2,
    "signals": {"sma_signal": "Golden Cross", "trend_phase": "bull"},
    "identity": {"name": "Mercedes-Benz Group AG", "region": "Germany"}
  }
}
```

Programmatic parity: `TradingAgentsGraph.propagate(ticker, date,
factor_context={...})`.

### Markets and tickers

TradingAgents works with any market Yahoo Finance covers, using the exchange-suffixed ticker. Company identity and the alpha benchmark resolve automatically per market.

- US: `AAPL`, `SPY`
- Hong Kong: `0700.HK` · Tokyo: `7203.T` · London: `AZN.L`
- India: `RELIANCE.NS`, `.BO` · Canada: `.TO` · Australia: `.AX`
- China A-shares: Shanghai `.SS`, Shenzhen `.SZ` (e.g. `600519.SS` for Kweichow Moutai)
- Crypto: `BTC-USD`, `ETH-USD`

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

## TradingAgents Package

### Implementation Details

We built TradingAgents with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, DeepSeek, Qwen (Alibaba DashScope, international and China endpoints), GLM (Zhipu), MiniMax (global + China), OpenRouter, Ollama for local models, and Azure OpenAI for enterprise.

### Python Usage

To use TradingAgents inside your code, you can import the `tradingagents` module and initialize a `TradingAgentsGraph()` object. The `.propagate()` function will return a decision. You can run `main.py`, here's also a quick example:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

You can also adjust the default configuration to set your own choice of LLMs, debate rounds, etc.

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # e.g. openai, google, anthropic, deepseek, groq, ollama; openai_compatible covers any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp, ...)
config["deep_think_llm"] = "gpt-5.5"     # Model for complex reasoning
config["quick_think_llm"] = "gpt-5.4-mini" # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

See `tradingagents/default_config.py` for all configuration options.

## Persistence and Recovery

TradingAgents persists two kinds of state across runs.

### Decision log

The decision log is always on. Each completed run appends its decision to `~/.tradingagents/memory/trading_memory.md`. On the next run for the same ticker, TradingAgents fetches the realised return (raw and alpha vs SPY), generates a one-paragraph reflection, and injects the most recent same-ticker decisions plus recent cross-ticker lessons into the Portfolio Manager prompt, so each analysis carries forward what worked and what didn't.

Override the path with `TRADINGAGENTS_MEMORY_LOG_PATH`.

### Checkpoint resume

Checkpoint resume is opt-in via `--checkpoint`. When enabled, LangGraph saves state after each node so a crashed or interrupted run resumes from the last successful step instead of starting over. On a resume run you will see `Resuming from step N for <TICKER> on <date>` in the logs; on a new run you will see `Starting fresh`. Checkpoints are cleared automatically on successful completion.

Per-ticker SQLite databases live at `~/.tradingagents/cache/checkpoints/<TICKER>.db` (override the base with `TRADINGAGENTS_CACHE_DIR`). Use `--clear-checkpoints` to reset all of them before a run.

```bash
tradingagents analyze --checkpoint           # enable for this run
tradingagents analyze --clear-checkpoints    # reset before running
```

```python
config = DEFAULT_CONFIG.copy()
config["checkpoint_enabled"] = True
ta = TradingAgentsGraph(config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
```

## Reproducibility

TradingAgents is LLM-driven, so two runs of the same ticker and date can differ. This is expected for a research tool built on language models, not a defect. The variation comes from a few distinct sources, and it helps to separate them.

Language model sampling is non-deterministic. Even at a fixed temperature, providers do not guarantee byte-identical output across calls, and reasoning models (the default GPT-5.x family, and any thinking-mode model) vary the most because their internal reasoning is itself sampled.

Live data moves. News, StockTwits, and Reddit return different content as time passes, so a run today sees different inputs than a run last week even for the same historical trade date. Pin the analysis date to hold the price and indicator window fixed, but the social and news sources still reflect "now".

Cross-run memory changes the second run. With the learning function enabled (default), each finished run stores its decision in a memory log, and the next run on the same ticker sees it — plus reflections on realized outcomes — as extra context. Two back-to-back runs therefore do not receive identical prompts even when the market data is identical.

Four levers reduce variation, all selectable in the web UI under **Erweiterte Einstellungen** and settable via config/env:

- **Temperature** — `temperature` / `TRADINGAGENTS_TEMPERATURE`. Lower values make models that honor it more repeatable. The curated default models are reasoning-first and largely ignore temperature; for tighter reproducibility use a non-reasoning model via the Custom model ID option.
- **Seed** — `seed` / `TRADINGAGENTS_SEED`. Forwarded only to providers whose API accepts one: Azure and OpenAI-compatible Chat Completions (deepseek, groq, ollama, custom endpoints, or `openai` with a custom base URL). Anthropic, Bedrock, Google, and the native OpenAI Responses API have no seed parameter and ignore it (with a warning). Even where supported, a seed makes runs more similar, not bit-identical.
- **Memory toggle** — `memory_enabled` / `TRADINGAGENTS_MEMORY_ENABLED`. Off means no past-decision context is injected and nothing is stored, so consecutive runs see identical inputs.
- **Per-day data cache** — `data_cache_daily` / `TRADINGAGENTS_DATA_CACHE_DAILY`. On means news/macro/fundamentals responses are cached per calendar day, so repeated runs on the same day see identical data (prices already have their own cache).

For a stable verdict rather than a stable transcript, use **ensemble mode** (`ensemble_runs` / `TRADINGAGENTS_ENSEMBLE_RUNS`, or "Anzahl Läufe" in the web UI): the full analysis runs N times and the final rating is the median of the per-run ratings, with vote counts reported alongside. Memory is read once before the first run and written once after aggregation, so all N runs see the same inputs. Note that N runs cost roughly N× the runtime and tokens, and a fixed seed can collapse the ensemble to near-identical runs on providers that honor it (the web UI offsets the seed per run to avoid this). Programmatic callers can use `TradingAgentsGraph.propagate_ensemble(...)`.

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["temperature"] = 0.0
config["memory_enabled"] = False    # identical inputs for back-to-back runs
config["data_cache_daily"] = True   # freeze news/macro/fundamentals per day
# Reasoning models ignore temperature. For tighter reproducibility, set a
# non-reasoning deep/quick model explicitly (e.g. via the Custom model ID option).

ta = TradingAgentsGraph(config=config)
final_state, decision, ensemble = ta.propagate_ensemble("NVDA", "2026-01-15", runs=3)
```

What does not vary anymore: the analyzed company identity is resolved deterministically from the ticker before any agent runs, and the market analyst grounds exact price and indicator claims in a verified data snapshot. Earlier reports of "different companies" or fabricated price levels across runs are addressed by these two mechanisms.

Backtest results are not guaranteed to match any published figure. Returns depend on the model, the temperature, the date range, data quality, and the sampling above. Treat the framework as a research scaffold for studying multi-agent analysis, not as a strategy with a fixed, replicable return.

## Contributing

Contributions are welcome: bug fixes, documentation, and feature ideas; past contributions are credited per release in [`CHANGELOG.md`](CHANGELOG.md).

## Citation

Please reference our work if you find *TradingAgents* provides you with some help :)

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework}, 
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138}, 
}
```
