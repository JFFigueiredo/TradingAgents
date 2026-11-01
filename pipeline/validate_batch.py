"""Parallel lean TradingAgents validation for a shortlist of tickers.

Designed to run inside the tradingagents Docker image (has the package + API key).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

# Reuse lean brevity patch BEFORE importing the graph.
import tradingagents.agents.context as agent_context

LEAN_RULE = (
    " BREVITY IS MANDATORY: keep every written reply to at most 5 short bullets"
    " (or ~80 words). No markdown tables, no multi-paragraph essays, no filler."
    " Still call the needed tools and reason carefully before writing."
    " Prefer decisive facts that change the trade call."
    " For the Portfolio Manager final answer: Rating line + one-sentence thesis only."
)
_orig_lang = agent_context.get_language_instruction


def _lean_language_instruction() -> str:
    return _orig_lang() + LEAN_RULE


agent_context.get_language_instruction = _lean_language_instruction

from langchain_core.callbacks import UsageMetadataCallbackHandler  # noqa: E402

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

PRICE_IN = 0.10
PRICE_OUT = 0.50
PRICE_CACHE_READ = 0.01


def _cost(meta: dict) -> float:
    inp = float(meta.get("input_tokens") or 0)
    out = float(meta.get("output_tokens") or 0)
    details = meta.get("input_token_details") or {}
    cache_read = float(details.get("cache_read") or details.get("cache_read_tokens") or 0)
    billable_in = max(inp - cache_read, 0.0)
    return (
        billable_in / 1_000_000 * PRICE_IN
        + cache_read / 1_000_000 * PRICE_CACHE_READ
        + out / 1_000_000 * PRICE_OUT
    )


def _trade_date() -> str:
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return os.environ.get("TRADE_DATE", d.isoformat())


def _to_action(rating: str) -> str:
    r = (rating or "").strip().lower()
    if r in {"buy", "overweight"}:
        return "BUY"
    if r in {"sell", "underweight"}:
        return "DO NOT BUY / REDUCE"
    if r == "hold":
        return "HOLD / NO NEW BUY"
    return f"REVIEW ({rating})"


def validate_one(ticker: str, trade_date: str, analysts: list[str]) -> dict:
    usage_cb = UsageMetadataCallbackHandler()
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "openai"
    config["deep_think_llm"] = "gpt-6-luna"
    config["quick_think_llm"] = "gpt-6-luna"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["max_tokens"] = int(os.environ.get("LEAN_MAX_TOKENS", "700"))
    config["news_article_limit"] = 8
    config["global_news_article_limit"] = 5
    config["max_tool_rounds"] = 8

    ta = TradingAgentsGraph(
        selected_analysts=analysts,
        debug=False,
        config=config,
        callbacks=[usage_cb],
    )
    _, decision = ta.propagate(ticker, trade_date)

    usage = dict(usage_cb.usage_metadata or {})
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "estimated_usd": 0.0,
    }
    for meta in usage.values():
        meta = dict(meta)
        details = meta.get("input_token_details") or {}
        cache_read = int(details.get("cache_read") or details.get("cache_read_tokens") or 0)
        totals["input_tokens"] += int(meta.get("input_tokens") or 0)
        totals["output_tokens"] += int(meta.get("output_tokens") or 0)
        totals["total_tokens"] += int(meta.get("total_tokens") or 0)
        totals["cache_read_tokens"] += cache_read
        totals["estimated_usd"] += _cost(meta)
    totals["estimated_usd"] = round(totals["estimated_usd"], 6)

    return {
        "ticker": ticker,
        "trade_date": trade_date,
        "action": _to_action(str(decision)),
        "rating": decision,
        "usage": totals,
        "ok": True,
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel lean TradingAgents validation")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers")
    parser.add_argument("--shortlist", type=Path, default=None, help="JSON shortlist file")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("TA_WORKERS", "2")))
    parser.add_argument(
        "--analysts",
        default=os.environ.get("ANALYSTS", "market,news,fundamentals"),
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    tickers: list[str] = []
    if args.shortlist and args.shortlist.exists():
        data = json.loads(args.shortlist.read_text(encoding="utf-8"))
        rows = data.get("shortlist", data if isinstance(data, list) else [])
        tickers.extend(str(r.get("ticker") or r).upper() for r in rows)
    if args.tickers.strip():
        tickers.extend(t.strip().upper() for t in args.tickers.split(",") if t.strip())
    tickers = sorted(set(t for t in tickers if t))
    if not tickers:
        print("No tickers to validate.", flush=True)
        return 1

    analysts = [a.strip() for a in args.analysts.split(",") if a.strip()]
    trade_date = _trade_date()
    workers = max(1, min(args.workers, len(tickers)))
    print(
        f"Validating {len(tickers)} tickers with {workers} workers @ {trade_date}",
        flush=True,
    )

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(validate_one, t, trade_date, analysts): t for t in tickers
        }
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001
                row = {
                    "ticker": t,
                    "trade_date": trade_date,
                    "action": "ERROR",
                    "rating": None,
                    "usage": {},
                    "ok": False,
                    "error": str(exc),
                }
            results.append(row)
            print(
                f"  {row['ticker']}: {row.get('action')} ({row.get('rating')})",
                flush=True,
            )

    results.sort(key=lambda r: r["ticker"])
    total_usd = round(sum((r.get("usage") or {}).get("estimated_usd") or 0 for r in results), 6)
    total_tokens = sum((r.get("usage") or {}).get("total_tokens") or 0 for r in results)
    report = {
        "trade_date": trade_date,
        "workers": workers,
        "count": len(results),
        "total_estimated_usd": total_usd,
        "total_tokens": total_tokens,
        "results": results,
    }
    text = json.dumps(report, indent=2, default=str)
    print("\n===== BATCH REPORT =====", flush=True)
    print(text, flush=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
