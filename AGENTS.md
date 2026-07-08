# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Evaluation
- `tradingagents/evaluation/benchmark.py` runs the full 10-agent pipeline across tickers/dates, measuring signal consistency (MAD across repeated runs) and directional accuracy (hit rate vs 20d/60d forward returns).
- Requires a configured LLM provider with valid API key (OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY). Without a key, only the unit tests (`tests/test_evaluation.py`) run.
- Scale down with `--tickers` and `--dates` flags; default is 10 tickers x 4 quarterly dates.
- Results written as JSON to `tradingagents/evaluation/results/`. Forward returns are fetched via yfinance (already a core dependency).
