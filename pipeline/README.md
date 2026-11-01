# Daytrade scan → Shortlist → TradingAgents

Built for **short-horizon / daytrading-style screening**, not 1-year chart patterns.

| Layer | Tool | Role |
|-------|------|------|
| **A** | `daytrade_scan` (default) | Fresh ≤7d intraday bars: RVOL spike, prev-day high/low break, gaps, range expansion |
| **B** | [tvscreener](https://github.com/deepentropy/tvscreener) | Session snapshot: relative volume + valuation |
| **Judge** | TradingAgents lean (Docker) | BUY / HOLD / NO on shortlist only |
| Optional | [stock-pattern](https://github.com/BennyThadikaran/stock-pattern) | `--scanner-a swing` for multi-week patterns |

Default merge: **AND** (must pass A and B).

## Data reality (important)

Free Yahoo + TradingView data is **fine for rotating scanners**, but:

- Often **delayed** (~1–15 minutes), not exchange-direct
- **Rate-limited** if you hammer thousands of symbols too fast
- **Not** good enough alone for precise live daytrade execution / HFT

So: use this to **find candidates**; use a real broker feed (Alpaca, IBKR, Polygon, etc.) if you later automate entries.

## Setup

```powershell
cd d:\Documentos\Projects\TradingAgents
pip install -r pipeline\requirements.txt
powershell -File pipeline\setup_vendor.ps1   # only needed for --scanner-a swing
docker compose build
```

## One-shot pipeline

```powershell
# Build liquid movers universe first (not SP500)
python pipeline\build_universe.py --refresh

# Daytrade A + tvscreener B only (TradingAgents off)
python pipeline\run_pipeline.py --no-ta --universe daytrade --interval 15m

# Faster bars
python pipeline\run_pipeline.py --no-ta --interval 5m

# Validate shortlist with 2 parallel TradingAgents workers
python pipeline\run_pipeline.py --ta-workers 2 --limit-validate 5 --fallback-b
```

## Continuous rotation (huge list, always refreshing)

```powershell
# Rotate S&P 500 in batches of 40, fresh 15m data each time, forever
python pipeline\rotate.py --batch-size 40 --sleep 3 --interval 15m

# Prefilter with tvscreener B, then rotate those; validate hits
python pipeline\rotate.py --prefilter-b --validate --limit-validate 3 --ta-workers 2 --loops 2
```

Each batch **re-downloads the latest ≤7 days** for those tickers (no stale 1y history).

## Daytrade triggers (A)

- `rvol_spike` — recent bars vs prior average volume  
- `break_prev_day_high` / `break_prev_day_low`  
- `gap_up` / `gap_down`  
- `range_expansion` — unusually wide bar + volume  

## Decision journal (auto-improve later)

Every **A and/or B positive** is appended to:

`pipeline\out\decision_journal.jsonl`

Even when TradingAgents says **NO / HOLD**, the row stays with:
- ticker, price at signal
- trigger sources (`A`, `B`, or both)
- A patterns / B volume & PE
- TA action + rating (if run)
- `outcome: null` until settled

Settle later (compare price after a horizon):

```powershell
python pipeline\settle_journal.py --horizon-hours 24 --summary
python pipeline\settle_journal.py --horizon-days 1 --summary
```

That scores whether a skip/buy looked correct — fuel for tuning filters later.
