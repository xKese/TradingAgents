# Vercel Deployment Guide

This guide covers deploying the TradingAgents framework to Vercel.

## Prerequisites

- Vercel account (https://vercel.com)
- Vercel CLI installed: `npm i -g vercel`
- Git repository pushed to GitHub

## Environment Variables

Before deploying, configure the following environment variables in Vercel:

### LLM Provider Keys (choose at least one)

- `OPENAI_API_KEY` - For GPT-5.x models
- `GOOGLE_API_KEY` - For Gemini 3.x models
- `ANTHROPIC_API_KEY` - For Claude 4.x models
- `XAI_API_KEY` - For Grok models
- `DEEPSEEK_API_KEY` - For DeepSeek models
- `DASHSCOPE_API_KEY` - For Qwen models
- `ZHIPU_API_KEY` - For GLM models
- `MINIMAX_API_KEY` - For MiniMax models
- `OPENROUTER_API_KEY` - For OpenRouter API access

### Optional Configuration

- `TRADINGAGENTS_LLM_PROVIDER` - Default LLM provider (e.g., `openai`, `anthropic`, `google`)
- `TRADINGAGENTS_DEEP_THINK_LLM` - Deep thinking model (default: provider-dependent)
- `TRADINGAGENTS_QUICK_THINK_LLM` - Quick thinking model (default: provider-dependent)
- `TRADINGAGENTS_OUTPUT_LANGUAGE` - Output language (default: `English`)
- `TRADINGAGENTS_MAX_DEBATE_ROUNDS` - Maximum debate rounds (default: `1`)
- `TRADINGAGENTS_MAX_RISK_ROUNDS` - Maximum risk assessment rounds (default: `1`)
- `TRADINGAGENTS_TEMPERATURE` - LLM sampling temperature (default: provider-dependent)
- `TRADINGAGENTS_CHECKPOINT_ENABLED` - Enable LangGraph checkpointing (default: `false`)

## Deployment Steps

### 1. Push to GitHub

Ensure your code is pushed to a GitHub repository:

```bash
git push origin claude/vercel-deploy-prep-xhq4u6
```

### 2. Connect to Vercel

Option A: Web Dashboard
1. Go to https://vercel.com/dashboard
2. Click "Add New..." → "Project"
3. Select your GitHub repository
4. Configure environment variables
5. Deploy

Option B: Vercel CLI
```bash
vercel
```

### 3. Configure Environment Variables

In Vercel Dashboard:
1. Go to Settings → Environment Variables
2. Add all required API keys
3. Select environments (Production, Preview, Development)

### 4. Deploy

```bash
vercel --prod
```

## API Endpoints

Once deployed, the following endpoints are available:

### Health Check
```
GET /api/health
```

Response:
```json
{
  "status": "healthy",
  "service": "TradingAgents",
  "version": "0.2.5"
}
```

## Serverless Function Specifications

- **Runtime**: Python 3.12
- **Memory**: 3008 MB (3 GB)
- **Max Duration**: 900 seconds (15 minutes)
- **Cold Start**: Optimized with pip caching

## Deployment Considerations

### Size Optimization

The `.vercelignore` file excludes:
- Test files and directories
- Documentation and markdown files
- Docker files
- Development dependencies
- IDE configuration files
- Cache and log files

This reduces deployment size from ~500MB to ~100-150MB.

### Dependencies

All dependencies are installed from `pyproject.toml` during the build phase:
- LangChain and integrations
- Backtrader for backtesting
- Financial data providers (yfinance, stockstats)
- Supporting libraries (pandas, requests, etc.)

### Performance Tips

1. **Cold Start Optimization**: The first request may take 30-60 seconds
2. **Caching**: Vercel automatically caches pip dependencies
3. **Concurrency**: Serverless functions can be concurrent but share state via Redis (if configured)
4. **Timeouts**: Long-running analysis may hit the 15-minute limit

## Redis Support

For state persistence across requests, configure a Redis instance:

```bash
# Set the Redis URL in environment variables
REDIS_URL=redis://user:password@host:port
```

Then enable checkpointing in your config:
```
TRADINGAGENTS_CHECKPOINT_ENABLED=true
```

## Monitoring and Logs

View deployment logs in the Vercel Dashboard:
1. Go to Deployments
2. Select a deployment
3. View Real-time Logs

## Troubleshooting

### Build Failures

Check logs for common issues:
- Missing API keys in environment variables
- Incompatible Python versions
- Missing build dependencies

### Runtime Errors

1. Check serverless function logs
2. Verify API keys are correctly set
3. Check function memory allocation
4. Ensure timeout is sufficient for your workload

### Performance Issues

1. Monitor function duration
2. Check for cold starts
3. Optimize dependency imports
4. Consider using Regional Deployment

## Scaling

Vercel automatically scales serverless functions. For high-traffic scenarios:
- Use concurrent requests carefully (stateful apps)
- Consider using Redis for distributed state
- Monitor usage in the Vercel Dashboard
- Upgrade plan if needed for higher concurrency

## Further Reading

- [Vercel Python Runtime Docs](https://vercel.com/docs/functions/serverless-functions/python)
- [Vercel Environment Variables](https://vercel.com/docs/projects/environment-variables)
- [TradingAgents README](./README.md)
