# StrattonOak Dashboard & API Deployment

This document explains how to deploy the **interactive dashboard** and **trading analysis API** to Vercel.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User Browser                             │
└────────────────────────┬────────────────────────────────────┘
                         │
                    ┌────▼─────────────────────────────────┐
                    │  Dashboard (Vercel)                 │
                    │  - Next.js React app                │
                    │  - strattonoak-dashboard.vercel.app │
                    │  - Calls /api/analyze               │
                    └────┬─────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
        ┌─────▼──────────┐    ┌────▼──────────────┐
        │ Health Check   │    │ Analysis API      │
        │ (Vercel)       │    │ (Vercel or own)   │
        │ /api/health    │    │ /api/analyze      │
        └────────────────┘    └────┬──────────────┘
                                   │
                         ┌─────────▼────────────┐
                         │ Trading Engine       │
                         │ (Docker/Railway)     │
                         │ TradingAgentsGraph   │
                         └──────────────────────┘
```

## Two Deployment Options

### Option A: Full Stack on Vercel (Recommended for demo/testing)

Deploy **both** dashboard and API to the same Vercel project.

**Pros:**
- Single project to manage
- Dashboard and API in sync
- Simpler configuration

**Cons:**
- API inherits Vercel's 15-min timeout (may be tight for multi-agent analysis)
- Functions can't be long-running

**Setup:**

```bash
# 1. Push to your repo
git push origin main

# 2. Deploy main Vercel project (includes health + analyze endpoints)
vercel --prod

# 3. Deploy dashboard as a separate project
cd dashboard
vercel --prod --name strattonoak-dashboard

# 4. In dashboard vercel.json, set BACKEND_URL to main API
# e.g., https://strattonoak.vercel.app
```

### Option B: Dashboard on Vercel, API on Railway/Docker (Recommended for production)

**Pros:**
- Dashboard is fast (static/SSR on Vercel)
- API can run long-running LLM workflows
- Separation of concerns

**Cons:**
- Two separate deployments to manage
- Need to coordinate environment variables

**Setup:**

```bash
# 1. Deploy dashboard to Vercel
cd dashboard
vercel --prod --name strattonoak-dashboard

# 2. Deploy API separately (Docker on Railway, or always-on host)
# Option A: Docker on Railway
railway up  # if you have Railway CLI set up

# Option B: Self-hosted
docker-compose up -d

# 3. Set BACKEND_URL in dashboard Vercel env vars
# Dashboard Settings → Environment Variables
# BACKEND_URL = https://trading-api.railway.app  (or your URL)
```

## Detailed Setup: Option A (Easier)

### Step 1: Configure Main API on Vercel

The main `vercel.json` already includes both endpoints:

```json
{
  "framework": null,
  "functions": {
    "api/health.py": { "memory": 1024, "maxDuration": 60 },
    "api/analyze.py": { "memory": 3008, "maxDuration": 900 }
  }
}
```

### Step 2: Deploy Main Project

```bash
cd /path/to/AStrattonOak

# Add environment variables for the API
# (if using with a configured LLM provider)
export ANTHROPIC_API_KEY=sk-ant-...
export ALPACA_API_KEY=...
export ALPACA_SECRET_KEY=...

# Deploy to Vercel
vercel --prod
```

### Step 3: Deploy Dashboard

```bash
cd dashboard

# Create .env.local with the API URL
echo "BACKEND_URL=https://strattonoak.vercel.app" > .env.local
echo "NEXT_PUBLIC_API_URL=https://strattonoak.vercel.app" >> .env.local

# Deploy
vercel --prod --name strattonoak-dashboard
```

### Step 4: Configure Deployment Protection (Optional)

By default, Vercel protects endpoints. To make the API publicly accessible:

1. Vercel Dashboard → **strattonoak** project → **Settings**
2. **Deployment Protection** → Disable or add bypass for `/api/*` routes

## API Endpoint Reference

### POST /api/analyze

**Request:**
```json
{
  "ticker": "AAPL",
  "date": "2026-07-03",
  "provider": "anthropic",
  "analysts": ["market", "news", "sentiment"]
}
```

**Response:**
```json
{
  "ticker": "AAPL",
  "date": "2026-07-03",
  "timestamp": "2026-07-03T16:45:00.000000",
  "recommendation": "BUY",
  "analysis": { ... },
  "analysts": {
    "market": { "summary": "..." },
    "news": { "summary": "..." },
    ...
  }
}
```

**Status Codes:**
- `200` — Analysis complete
- `400` — Missing ticker or date
- `500` — Analysis failed (see error message)
- `503` — Trading framework not available

## Environment Variables

### Main API (vercel.json)

These are **optional** if the backend will prompt for values:

```env
# LLM Provider Keys
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...

# Alpaca (for live/paper trading)
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true

# Trading Framework Config
TRADINGAGENTS_LLM_PROVIDER=anthropic
TRADINGAGENTS_DEEP_THINK_LLM=claude-opus
TRADINGAGENTS_MAX_DEBATE_ROUNDS=2
```

### Dashboard (dashboard/.env.local)

```env
BACKEND_URL=https://strattonoak.vercel.app
NEXT_PUBLIC_API_URL=https://strattonoak.vercel.app
```

## Testing Locally

### Test the Analysis API

```bash
# Start the trading framework (if deployed locally)
python main.py

# In another terminal, test the API
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker":"AAPL","date":"2026-07-03","provider":"anthropic","analysts":["market","news"]}'
```

### Test the Dashboard Locally

```bash
cd dashboard
npm install
npm run dev
# Open http://localhost:3000
```

Set `BACKEND_URL` to point to your local or remote API in `.env.local`.

## Troubleshooting

### "Framework not available" error

The trading framework dependencies are excluded from Vercel to keep the build small. To use the `/api/analyze` endpoint:

1. **Option A:** Configure the API to run locally/on Railway and point dashboard to it
2. **Option B:** Install `pyproject.toml` on Vercel by removing it from `.vercelignore` (warning: may exceed 250 MB limit)

### Dashboard shows "Analysis failed"

1. Check browser DevTools → Network tab → see the actual error response
2. Verify `BACKEND_URL` is set correctly in Vercel environment variables
3. Ensure the API endpoint is reachable (check CORS headers)

### API times out on long analyses

The Vercel serverless timeout is 900 seconds (15 minutes). Adjust based on your needs:
- Update `"maxDuration"` in `vercel.json`
- Or deploy the API separately (Docker/Railway) for true long-running processes

## Next Steps

1. **Deploy to Vercel:** Follow Option A or B above
2. **Integrate with LLM:** Set up API keys in environment variables
3. **Monitor dashb board:** Use Vercel Analytics to track usage
4. **Set up uptime monitoring:** Use `/api/health` with a service like Uptime Robot

## Further Reading

- [Vercel Python Functions](https://vercel.com/docs/functions/serverless-functions/python)
- [Next.js Deployment on Vercel](https://nextjs.org/docs/deployment/vercel)
- [StrattonOak Framework](../README.md)
