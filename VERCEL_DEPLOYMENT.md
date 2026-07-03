# Vercel Deployment Guide

This repo is deployed to Vercel as a **lightweight API surface**, not as the full
trading engine. This document explains what is deployed, why, and how to operate it.

## What gets deployed

Vercel ships only:

- `public/index.html` — a static landing/status page served at `/`
- `api/health.py` — a stdlib-only Python serverless function served at `/api/health`

Everything else (the `tradingagents/` package, `cli/`, `scripts/`, `main.py`,
`pyproject.toml`, `requirements.txt`, `uv.lock`) is excluded from the Vercel build
via `.vercelignore`.

### Why the full framework is not deployed

The TradingAgents engine is **not suitable for serverless**:

- **Size:** its dependency set (langchain, backtrader, yfinance, pandas, multiple
  provider SDKs) far exceeds Vercel's 250 MB unzipped serverless function limit.
- **Runtime:** a single `propagate()` run drives multi-round LLM debates that take
  minutes — well beyond serverless execution windows, and not a fit for a
  request/response function.

The engine is meant to run as a long-lived process. Use the provided `Dockerfile` /
`docker-compose.yml` (or any always-on host) for the actual trading workloads. Vercel
here provides a public health/status surface only.

## Configuration

`vercel.json`:

```json
{
  "framework": null,
  "functions": {
    "api/health.py": { "memory": 1024, "maxDuration": 60 }
  }
}
```

- `"framework": null` disables Vercel's Python-framework single-entrypoint detection
  (which otherwise trips over the root `main.py` script). This keeps the project in
  zero-config serverless-function mode where `api/*.py` files with a `handler` are
  deployed automatically.
- No `buildCommand` and no build-time `pip install` — the health function needs only
  the Python standard library, so there is nothing to install.

## Endpoints

### `GET /` — Landing page
Static HTML status page.

### `GET /api/health` — Health check
```json
{
  "status": "healthy",
  "service": "TradingAgents",
  "version": "0.2.5",
  "timestamp": "2026-07-03T16:29:00.000000"
}
```

## Deployment Protection

By default, Vercel preview (and optionally production) deployments are gated behind
**Vercel Authentication**. Anonymous requests receive a `302` redirect to
`vercel.com/sso-api`. This is expected and is not an application error.

To make `/api/health` publicly reachable (e.g. for an external uptime monitor):

1. Vercel Dashboard → project **strattonoak** → **Settings → Deployment Protection**
2. Either disable Vercel Authentication, or add a **Protection Bypass** for the
   `/api/health` path / for automation.

## Environment Variables

The health function does not read any secrets. The following are configured on the
project for when real, engine-backed endpoints are added later (they are **not** used
by anything currently deployed):

- `ANTHROPIC_API_KEY`
- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `ALPACA_PAPER`

> Security: any key that was ever shared in plaintext (chat, commits, screenshots)
> should be rotated in the provider dashboard and only re-entered via Vercel's
> encrypted Environment Variables UI.

## Local check

The health handler is a standard `BaseHTTPRequestHandler`; you can sanity-check the
module imports and parses with:

```bash
python -c "import ast; ast.parse(open('api/health.py').read())"
```

## Redeploying

Pushes to the branch trigger a Vercel deployment automatically. Deployment status,
build logs, and runtime logs are available in the Vercel Dashboard under the
**strattonoak** project.
