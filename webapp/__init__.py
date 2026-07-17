"""Local web UI for TradingAgents.

A small FastAPI app that serves a browser front-end for running the
multi-agent trading pipeline locally. It reuses the CLI's provider/model
catalogs, the graph streaming pattern, and the shared report writer, so a
browser run produces the same results and on-disk reports a CLI run does.

Install the optional dependencies with ``pip install ".[web]"`` and start it
with ``tradingagents serve``.
"""
