"""Systematic evaluation harness for TradingAgents.

Runs the 10-agent bull/bear debate pipeline on multiple tickers and quarterly
dates. Measures signal consistency (mean absolute deviation across repeated
runs), directional accuracy (hit rate of consensus direction vs 20d/60d
forward returns), and false positive rate.

Results are written to ``tradingagents/evaluation/results/`` as JSON.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import yfinance as yf

from tradingagents.agents.utils.rating import RATINGS_5_TIER, parse_rating
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("evaluation")

_RATING_TO_SCORE: dict[str, int] = {
    "Buy": 2, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -2,
}

_DEFAULT_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "JPM", "BAC",
    "JNJ", "PFE", "WMT", "KO", "XOM",
]

_QUARTERLY_DATES_2023_2025: list[str] = [
    "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
    "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
    "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
]

_RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _score_from_rating(rating: str) -> int:
    for r in RATINGS_5_TIER:
        if r.lower() == rating.lower():
            return _RATING_TO_SCORE[r]
    return 0


def _run_single(
    graph: TradingAgentsGraph, ticker: str, trade_date: str, run_index: int = 0,
) -> dict[str, Any] | None:
    try:
        _, decision = graph.propagate(ticker, trade_date)
        rating = parse_rating(decision)
        score = _score_from_rating(rating)
        return {
            "ticker": ticker,
            "date": trade_date,
            "run": run_index,
            "decision": decision,
            "rating": rating,
            "score": score,
            "error": None,
        }
    except Exception as exc:
        logger.warning("Run failed %s %s run=%d: %s", ticker, trade_date, run_index, exc)
        return {
            "ticker": ticker,
            "date": trade_date,
            "run": run_index,
            "decision": None,
            "rating": None,
            "score": None,
            "error": str(exc),
        }


def _fetch_forward_returns(ticker: str, trade_date: str) -> dict[str, float | None]:
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        end_60d = start + timedelta(days=90)  # buffer for non-trading days
        stock = yf.Ticker(ticker).history(start=trade_date, end=end_60d.strftime("%Y-%m-%d"))
        if len(stock) < 2:
            return {"ret_20d": None, "ret_60d": None}

        close = stock["Close"]
        base = float(close.iloc[0])

        ret20 = None
        if len(close) >= 20:
            ret20 = float((close.iloc[min(19, len(close) - 1)] - base) / base)

        ret60 = None
        if len(close) >= 60:
            ret60 = float((close.iloc[min(59, len(close) - 1)] - base) / base)
        elif len(close) >= 20:
            ret60 = float((close.iloc[-1] - base) / base)

        return {"ret_20d": ret20, "ret_60d": ret60}
    except Exception as exc:
        logger.warning("Price fetch failed for %s on %s: %s", ticker, trade_date, exc)
        return {"ret_20d": None, "ret_60d": None}


def _compute_consistency(runs: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [r["score"] for r in runs if r["score"] is not None]
    if len(scores) < 2:
        return {
            "n_runs": len(scores),
            "mean_score": mean(scores) if scores else None,
            "stdev": None,
            "mean_absolute_deviation": None,
            "all_ratings": [r["rating"] for r in runs],
            "all_same_rating": None,
        }

    avg = mean(scores)
    deviations = [abs(s - avg) for s in scores]
    ratings = [r["rating"] for r in runs]
    return {
        "n_runs": len(scores),
        "mean_score": avg,
        "stdev": stdev(scores),
        "mean_absolute_deviation": mean(deviations),
        "all_ratings": ratings,
        "all_same_rating": len(set(r.lower() for r in ratings if r)) <= 1,
    }


def _compute_directional_metrics(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    hits_20d = 0
    hits_60d = 0
    total_20d = 0
    total_60d = 0
    false_positives_20d = 0
    false_positives_60d = 0
    bullish_with_return = 0

    for r in results:
        if r.get("error"):
            continue
        score = r.get("score")
        if score is None:
            continue

        fwd = r.get("forward_returns", {})
        direction = 1 if score > 0 else (-1 if score < 0 else 0)
        if direction == 0:
            continue

        ret20 = fwd.get("ret_20d")
        if ret20 is not None:
            total_20d += 1
            if (direction > 0 and ret20 > 0) or (direction < 0 and ret20 < 0):
                hits_20d += 1
            if direction > 0 and ret20 <= 0:
                false_positives_20d += 1
                bullish_with_return += 1
            elif direction > 0:
                bullish_with_return += 1

        ret60 = fwd.get("ret_60d")
        if ret60 is not None:
            total_60d += 1
            if (direction > 0 and ret60 > 0) or (direction < 0 and ret60 < 0):
                hits_60d += 1
            if direction > 0 and ret60 <= 0:
                false_positives_60d += 1

    return {
        "hit_rate_20d": hits_20d / total_20d if total_20d > 0 else None,
        "hit_rate_60d": hits_60d / total_60d if total_60d > 0 else None,
        "total_predictions_20d": total_20d,
        "total_predictions_60d": total_60d,
        "false_positive_rate_20d": (false_positives_20d / bullish_with_return
                                     if bullish_with_return > 0 else None),
        "false_positive_rate_60d": (false_positives_60d / bullish_with_return
                                     if bullish_with_return > 0 else None),
        "bullish_predictions": bullish_with_return,
        "false_positives_20d": false_positives_20d,
        "false_positives_60d": false_positives_60d,
    }


def run_benchmark(
    tickers: list[str] | None = None,
    dates: list[str] | None = None,
    n_runs: int = 2,
    config: dict[str, Any] | None = None,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    tickers = tickers or _DEFAULT_TICKERS
    dates = dates or _QUARTERLY_DATES_2023_2025[-4:]  # default: last 4 quarters
    results_dir = Path(results_dir or _RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)

    cfg = (config or DEFAULT_CONFIG).copy()
    cfg.setdefault("max_debate_rounds", 1)
    cfg.setdefault("max_risk_discuss_rounds", 1)

    all_runs: list[dict[str, Any]] = []
    consistency_by_key: dict[str, Any] = {}

    graph = TradingAgentsGraph(debug=False, config=cfg)

    for ticker in tickers:
        for trade_date in dates:
            key = f"{ticker}_{trade_date}"
            logger.info("Evaluating %s", key)

            runs_for_key = []
            for i in range(n_runs):
                result = _run_single(graph, ticker, trade_date, run_index=i)
                runs_for_key.append(result)
                all_runs.append(result)

            forward = _fetch_forward_returns(ticker, trade_date)
            for r in runs_for_key:
                r["forward_returns"] = forward

            consistency_by_key[key] = _compute_consistency(runs_for_key)

    scores = [r["score"] for r in all_runs if r["score"] is not None]
    consistent_keys = sum(
        1 for v in consistency_by_key.values()
        if v.get("all_same_rating") is True
    )
    total_keys = len(consistency_by_key)

    mad_values = [
        v["mean_absolute_deviation"] for v in consistency_by_key.values()
        if v["mean_absolute_deviation"] is not None
    ]

    directional = _compute_directional_metrics(all_runs)

    summary = {
        "benchmark_config": {
            "tickers": tickers,
            "dates": dates,
            "n_runs_per_combination": n_runs,
            "total_combinations": len(tickers) * len(dates),
            "total_runs": len(all_runs),
        },
        "consistency": {
            "overall_mean_absolute_deviation": mean(mad_values) if mad_values else None,
            "overall_stdev_of_scores": stdev(scores) if len(scores) >= 2 else None,
            "fraction_consistent": (consistent_keys / total_keys if total_keys > 0 else None),
            "total_keys": total_keys,
            "consistent_keys": consistent_keys,
            "per_key": {k: v for k, v in consistency_by_key.items()},
        },
        "direction": directional,
        "score_distribution": _score_distribution(all_runs),
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    summary_path = results_dir / f"summary_{timestamp}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    runs_path = results_dir / f"runs_{timestamp}.json"
    with open(runs_path, "w", encoding="utf-8") as f:
        json.dump(all_runs, f, indent=2, default=str)

    print(f"Summary written to {summary_path}")
    print(f"Raw runs written to {runs_path}")
    _print_summary(summary)

    return summary


def _score_distribution(runs: list[dict[str, Any]]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in runs:
        rating = r.get("rating")
        if rating:
            dist[rating] = dist.get(rating, 0) + 1
        else:
            dist["error"] = dist.get("error", 0) + 1
    return dist


def _print_summary(summary: dict[str, Any]) -> None:
    cfg = summary["benchmark_config"]
    con = summary["consistency"]
    dir_ = summary["direction"]

    print("\n=== TradingAgents Evaluation Summary ===")
    print(f"Tickers: {cfg['tickers']}")
    print(f"Dates: {cfg['dates']}")
    print(f"Runs per combination: {cfg['n_runs_per_combination']}")
    print(f"Total runs: {cfg['total_runs']}")
    print()

    print("--- Consistency ---")
    print(f"  Overall MAD: {con['overall_mean_absolute_deviation']}")
    print(f"  Fraction consistent: {con['fraction_consistent']}")
    print(f"  Score stdev: {con['overall_stdev_of_scores']}")
    print()

    print("--- Directional Accuracy ---")
    print(f"  Hit rate (20d): {dir_.get('hit_rate_20d')}")
    print(f"  Hit rate (60d): {dir_.get('hit_rate_60d')}")
    print(f"  False positive rate (20d): {dir_.get('false_positive_rate_20d')}")
    print(f"  False positive rate (60d): {dir_.get('false_positive_rate_60d')}")
    print()

    print("--- Score Distribution ---")
    for rating, count in sorted(summary.get("score_distribution", {}).items(),
                                key=lambda x: x[1], reverse=True):
        print(f"  {rating}: {count}")
    print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TradingAgents Evaluation Benchmark")
    parser.add_argument("--tickers", nargs="*", default=None,
                        help="Tickers to evaluate (default: 10-ticker set)")
    parser.add_argument("--dates", nargs="*", default=None,
                        help="Dates in YYYY-MM-DD format (default: last 4 quarterly)")
    parser.add_argument("--n-runs", type=int, default=2,
                        help="Repeated runs per ticker-date combination (default: 2)")
    parser.add_argument("--results-dir", type=str, default=None,
                        help="Output directory for results JSON")
    args = parser.parse_args()

    run_benchmark(
        tickers=args.tickers or None,
        dates=args.dates or None,
        n_runs=args.n_runs,
        results_dir=Path(args.results_dir) if args.results_dir else None,
    )
