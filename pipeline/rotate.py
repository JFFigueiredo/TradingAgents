#!/usr/bin/env python3
"""Continuously rotate through a large universe looking for daytrade triggers.

Designed for short-horizon scanning: each ticker is re-fetched with fresh ≤7d
intraday bars when its batch comes up. Surviving hits can optionally be pushed
through lean TradingAgents.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = Path(__file__).resolve().parent
OUT_DIR = PIPELINE / "out"
sys.path.insert(0, str(PIPELINE))

from journal import attach_ta_results, build_cases, log_cases  # noqa: E402
from scanners.daytrade_scan import DaytradeConfig, scan_daytrade  # noqa: E402
from scanners.shortlist import filter_quality_buys, merge_shortlist  # noqa: E402
from scanners.tvscreener_scan import TvFilters, list_index_tickers, screen_to_records  # noqa: E402
from scanners.universe import (  # noqa: E402
    DaytradeUniverseFilters,
    default_universe_path,
    ensure_daytrade_universe,
    load_universe_file,
)
from learn_adapt import DEFAULT_CONFIG as ADAPT_CONFIG_PATH  # noqa: E402
from learn_adapt import load_config as load_adapt_config  # noqa: E402
from learn_adapt import run_adapt  # noqa: E402
from trade_rules import (  # noqa: E402
    ExitPlan,
    SessionConfig,
    SizingConfig,
    in_trade_window,
    rank_and_size,
    session_label,
    spy_regime_ok,
)


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _validate(shortlist: list[dict], workers: int) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    inp = OUT_DIR / f"rotate_validate_input_{stamp}.json"
    out = OUT_DIR / f"rotate_validate_{stamp}.json"
    inp.write_text(json.dumps({"shortlist": shortlist}, indent=2), encoding="utf-8")
    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "-v",
        f"{PIPELINE / 'validate_batch.py'}:/tmp/validate_batch.py:ro",
        "-v",
        f"{inp}:/tmp/shortlist.json:ro",
        "-v",
        f"{OUT_DIR}:/tmp/out",
        "--entrypoint",
        "python",
        "tradingagents",
        "/tmp/validate_batch.py",
        "--shortlist",
        "/tmp/shortlist.json",
        "--workers",
        str(workers),
        "--out",
        f"/tmp/out/{out.name}",
    ]
    print("Validating:", [r["ticker"] for r in shortlist], flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    p = argparse.ArgumentParser(description="Rotating daytrade scanner")
    p.add_argument("--index", default="SP500", help="Legacy index if not using daytrade universe")
    p.add_argument(
        "--universe",
        choices=["daytrade", "sp500", "file"],
        default="daytrade",
    )
    p.add_argument("--refresh-universe", action="store_true")
    p.add_argument("--universe-file", type=Path, default=None)
    p.add_argument("--batch-size", type=int, default=40)
    p.add_argument("--sleep", type=float, default=3.0, help="Seconds between batches")
    p.add_argument("--loops", type=int, default=0, help="0 = forever (unless --max-minutes)")
    p.add_argument(
        "--max-minutes",
        type=float,
        default=0,
        help="Stop after N minutes (0 = no time limit)",
    )
    p.add_argument("--interval", default="15m", help="5m | 15m | 30m | 60m")
    p.add_argument("--min-rvol", type=float, default=1.8)
    p.add_argument("--prefilter-b", action="store_true",
                   help="Only rotate names that currently pass tvscreener B filters")
    p.add_argument("--rvol-b", type=float, default=2.5, help="tvscreener RVOL for B / buy quality")
    p.add_argument("--buy-max-rvol", type=float, default=5.0, help="Cap weak high-RVOL band")
    p.add_argument("--buy-min-price", type=float, default=5.0)
    p.add_argument("--buy-max-price", type=float, default=200.0)
    p.add_argument(
        "--require-range-expansion",
        action="store_true",
        help="Also require range_expansion pattern (higher win-rate, fewer fills)",
    )
    p.add_argument("--validate", action="store_true", help="Send hits to TradingAgents every loop")
    p.add_argument(
        "--validate-every-minutes",
        type=float,
        default=0,
        help="Run TradingAgents on current BUYs every N minutes (0 = off unless --validate)",
    )
    p.add_argument("--ta-workers", type=int, default=2)
    p.add_argument("--limit-validate", type=int, default=5)
    p.add_argument("--mode", choices=["and", "or"], default="and",
                   help="With --prefilter-b: and=require B+daytrade, or=either")
    p.add_argument(
        "--loop-sleep",
        type=float,
        default=15.0,
        help="Seconds between loops (keep low; Yahoo/TV rate limits are the real throttle)",
    )
    p.add_argument(
        "--journal-cooldown-minutes",
        type=float,
        default=15.0,
        help="Only re-journal the same ticker within this window if patterns change",
    )
    p.add_argument(
        "--status-every-minutes",
        type=float,
        default=10.0,
        help="Print a STATUS summary every N minutes (0 = off)",
    )
    p.add_argument(
        "--quality-buys",
        action="store_true",
        default=True,
        help="BUY = gap_up+break_high, no bear patterns, rvol in band (tuned filter)",
    )
    p.add_argument(
        "--loose-buys",
        action="store_true",
        help="Disable quality filter; any A∩B shortlist row counts as BUY",
    )
    p.add_argument("--top-n", type=int, default=3, help="Keep top N quality buys (concentrated)")
    p.add_argument("--open-window-minutes", type=int, default=90,
                   help="Only open buys in first N min after 9:30 ET (0=all RTH)")
    p.add_argument("--allow-premarket", action="store_true")
    p.add_argument("--allow-afterhours", action="store_true")
    p.add_argument("--require-spy-green", action="store_true", default=True,
                   help="Skip new buys when SPY day-change is below --spy-min-chg-pct")
    p.add_argument("--no-spy-filter", action="store_true",
                   help="Disable SPY regime filter")
    p.add_argument(
        "--spy-min-chg-pct",
        type=float,
        default=-0.1,
        help="Allow SPY as soft as this %% vs open (0=must be green)",
    )
    p.add_argument("--take-profit-pct", type=float, default=2.25)
    p.add_argument("--stop-loss-pct", type=float, default=1.0)
    p.add_argument("--time-stop-minutes", type=float, default=30.0)
    p.add_argument("--confirm-minutes", type=float, default=2.0)
    p.add_argument(
        "--adapt-every-minutes",
        type=float,
        default=60.0,
        help="Re-learn params from journal every N minutes (0=off)",
    )
    p.add_argument(
        "--adapt-config",
        type=Path,
        default=ADAPT_CONFIG_PATH,
        help="Path to adaptive_config.json",
    )
    args = p.parse_args()
    if args.loose_buys:
        args.quality_buys = False
    if args.no_spy_filter:
        args.require_spy_green = False

    def _apply_adapt(cfg: dict) -> tuple[SessionConfig, SizingConfig, ExitPlan]:
        if cfg.get("min_rvol") is not None:
            args.rvol_b = float(cfg["min_rvol"])
        if cfg.get("max_rvol") is not None:
            args.buy_max_rvol = float(cfg["max_rvol"])
        if cfg.get("top_n") is not None:
            args.top_n = int(cfg["top_n"])
        if cfg.get("open_window_minutes") is not None:
            args.open_window_minutes = int(cfg["open_window_minutes"])
        if cfg.get("require_spy_green") is not None and not args.no_spy_filter:
            args.require_spy_green = bool(cfg["require_spy_green"])
        if cfg.get("spy_min_chg_pct") is not None:
            args.spy_min_chg_pct = float(cfg["spy_min_chg_pct"])
        if cfg.get("require_range_expansion") is not None:
            args.require_range_expansion = bool(cfg["require_range_expansion"])
        if cfg.get("take_profit_pct") is not None:
            args.take_profit_pct = float(cfg["take_profit_pct"])
        if cfg.get("stop_loss_pct") is not None:
            args.stop_loss_pct = float(cfg["stop_loss_pct"])
        if cfg.get("time_stop_minutes") is not None:
            args.time_stop_minutes = float(cfg["time_stop_minutes"])
        session = SessionConfig(
            allow_premarket=args.allow_premarket,
            allow_afterhours=args.allow_afterhours,
            open_window_minutes=args.open_window_minutes,
        )
        sizing = SizingConfig(top_n=max(1, args.top_n))
        exits = ExitPlan(
            take_profit_pct=args.take_profit_pct,
            stop_loss_pct=args.stop_loss_pct,
            time_stop_minutes=args.time_stop_minutes,
            confirm_minutes=args.confirm_minutes,
        )
        return session, sizing, exits

    # Seed from prior adaptive config if present
    adapt_cfg = load_adapt_config(args.adapt_config)
    session_cfg, sizing_cfg, exit_cfg = _apply_adapt(adapt_cfg)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.universe == "file" or (args.universe_file and args.universe_file.exists()):
        path = args.universe_file or default_universe_path()
        universe = load_universe_file(path)
        print(f"Universe file: {len(universe)} ({path})", flush=True)
    elif args.universe == "daytrade":
        print("Loading daytrade liquid universe…", flush=True)
        universe = ensure_daytrade_universe(
            default_universe_path(),
            DaytradeUniverseFilters(),
            refresh=args.refresh_universe,
        )
        print(f"Daytrade universe: {len(universe)}", flush=True)
        if args.prefilter_b:
            tv = screen_to_records(
                TvFilters(index="NONE", min_rel_volume=args.rvol_b, pe_min=None, pe_max=None, max_rows=500)
            )
            bset = {r["ticker"] for r in tv}
            universe = [t for t in universe if t in bset] or universe
            print(f"After B prefilter: {len(universe)}", flush=True)
    elif args.prefilter_b:
        print("Building universe from tvscreener B prefilter…", flush=True)
        tv = screen_to_records(
            TvFilters(index=args.index, min_rel_volume=args.rvol_b, max_rows=500)
        )
        universe = [r["ticker"] for r in tv]
        print(f"B prefilter size: {len(universe)}", flush=True)
    else:
        print(f"Loading {args.index} constituents…", flush=True)
        universe = list_index_tickers(args.index, max_rows=500)

    if not universe:
        print("Empty universe.", flush=True)
        return 1

    cfg = DaytradeConfig(interval=args.interval, min_rvol=args.min_rvol)
    loop = 0
    started = time.time()
    deadline = (
        started + args.max_minutes * 60.0 if args.max_minutes and args.max_minutes > 0 else None
    )
    last_validate_at = 0.0  # force first eligible loop to validate when interval set
    last_status_at = 0.0
    last_adapt_at = 0.0
    # ticker -> (patterns_tuple, last_journal_ts)
    journaled: dict[str, tuple[tuple[str, ...], float]] = {}
    loops_since_status = 0
    buys_seen: set[str] = set()
    validate_interval_s = (
        args.validate_every_minutes * 60.0
        if args.validate_every_minutes and args.validate_every_minutes > 0
        else 0.0
    )
    journal_cooldown_s = max(0.0, args.journal_cooldown_minutes) * 60.0
    status_every_s = (
        args.status_every_minutes * 60.0
        if args.status_every_minutes and args.status_every_minutes > 0
        else 0.0
    )
    adapt_every_s = (
        args.adapt_every_minutes * 60.0
        if args.adapt_every_minutes and args.adapt_every_minutes > 0
        else 0.0
    )
    print(
        f"Rotating continuously | universe≈{len(universe)} | batch={args.batch_size} "
        f"| interval={args.interval} | batch_sleep={args.sleep}s | loop_sleep={args.loop_sleep}s"
        + (f" | max_minutes={args.max_minutes}" if deadline else ""),
        flush=True,
    )
    print(
        "NOTE: Yahoo/TradingView free data is delayed and rate-limited — "
        "OK for screening, not exchange-grade daytrade execution.",
        flush=True,
    )
    if validate_interval_s > 0:
        print(
            f"TradingAgents: validate BUYs every {args.validate_every_minutes:.0f} min "
            f"(top {args.limit_validate}, workers={args.ta_workers}).",
            flush=True,
        )
    elif args.validate:
        print("TradingAgents: ON every loop.", flush=True)
    else:
        print("TradingAgents: OFF — continuous A∩B scan only.", flush=True)
    print(
        f"Profit rules: top_n={sizing_cfg.top_n} | open_window={session_cfg.open_window_minutes}m ET "
        f"| spy_green={args.require_spy_green} | min_rvol={args.rvol_b} "
        f"| TP={exit_cfg.take_profit_pct}% SL={exit_cfg.stop_loss_pct}% "
        f"time_stop={exit_cfg.time_stop_minutes}m confirm={exit_cfg.confirm_minutes}m",
        flush=True,
    )
    if adapt_every_s > 0:
        print(
            f"LEARN/ADAPT every {args.adapt_every_minutes:.0f} min → {args.adapt_config}",
            flush=True,
        )
    if status_every_s > 0:
        print(f"STATUS heartbeat every {args.status_every_minutes:.0f} min.", flush=True)

    while True:
        loop += 1
        if args.loops and loop > args.loops:
            break
        if deadline is not None and time.time() >= deadline:
            print(f"Reached --max-minutes={args.max_minutes}. Stopping.", flush=True)
            break
        print(f"\n===== ROTATION LOOP {loop} @ {datetime.now(timezone.utc).isoformat()} =====", flush=True)

        # Periodic learn → retune filters from settled journal outcomes
        if adapt_every_s > 0 and (time.time() - last_adapt_at) >= adapt_every_s:
            try:
                print("ADAPT: scoring journal and updating config…", flush=True)
                adapt_cfg = run_adapt(out=args.adapt_config)
                session_cfg, sizing_cfg, exit_cfg = _apply_adapt(adapt_cfg)
                last_adapt_at = time.time()
                print(
                    f"ADAPT applied: min_rvol={args.rvol_b} max_rvol={args.buy_max_rvol} "
                    f"top_n={sizing_cfg.top_n} TP={exit_cfg.take_profit_pct} "
                    f"SL={exit_cfg.stop_loss_pct} time_stop={exit_cfg.time_stop_minutes} "
                    f"| sample={adapt_cfg.get('sample_n')} win={adapt_cfg.get('win_rate')} "
                    f"avg={adapt_cfg.get('avg_return')} notes={adapt_cfg.get('notes')}",
                    flush=True,
                )
                # Offline lab: refresh variant leaderboard (does NOT change live rules)
                try:
                    from lab_backtest import run_lab  # noqa: WPS433

                    lab = run_lab()
                    best = lab.get("best") or {}
                    print(
                        f"LAB: best={best.get('id')} "
                        f"exp={best.get('avg_ret_exit')} "
                        f"50e→{best.get('equity_50_exit')} "
                        f"(live stays baseline_v1; see out/lab_leaderboard.json)",
                        flush=True,
                    )
                except Exception as lab_exc:  # noqa: BLE001
                    print(f"LAB refresh skipped: {lab_exc}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"ADAPT failed (continuing with prior rules): {exc}", flush=True)
                last_adapt_at = time.time()

        # Refresh B snapshot each loop when using AND (A∩B buy candidates)
        tv_rows: list[dict] = []
        if args.prefilter_b or args.mode == "and":
            b_index = "NONE" if args.universe == "daytrade" else args.index
            # Match daytrade universe floor ($300M) so B can actually intersect A
            b_mcap = 300e6 if args.universe == "daytrade" else 2e9
            tv_rows = screen_to_records(
                TvFilters(
                    index=b_index,
                    min_rel_volume=args.rvol_b,
                    min_market_cap=b_mcap,
                    pe_min=None,
                    pe_max=None,
                    max_rows=500,
                )
            )
            print(f"B snapshot: {len(tv_rows)} names (index={b_index})", flush=True)
            print(f"  B tickers: {[r['ticker'] for r in tv_rows[:40]]}", flush=True)

        # AND mode: only scan B names (A∩B buys); fall back to universe slice if B empty
        scan_list = universe
        if args.mode == "and":
            if tv_rows:
                uni_set = set(universe)
                in_uni = [r["ticker"] for r in tv_rows if r["ticker"] in uni_set]
                # Still scan all B names — they are the live volume screen
                scan_list = [r["ticker"] for r in tv_rows]
                print(
                    f"AND: scanning {len(scan_list)} B names "
                    f"({len(in_uni)} also in daytrade universe)",
                    flush=True,
                )
            else:
                scan_list = universe[:80]
                print("AND: B empty — scanning first 80 universe names", flush=True)

        all_hits: list[dict] = []
        stop_early = False
        for batch_i, batch in enumerate(_chunks(scan_list, args.batch_size), start=1):
            if deadline is not None and time.time() >= deadline:
                print("Time limit hit mid-loop; finishing this partial pass.", flush=True)
                stop_early = True
                break
            print(f"Batch {batch_i}: {len(batch)} names (fresh {args.interval} data)…", flush=True)
            hits = scan_daytrade(batch, cfg)
            all_hits.extend(hits)
            for h in hits:
                print(
                    f"  HIT {h['ticker']} {h['pattern']} @ {h.get('price')} "
                    f"(asof {h.get('asof')})",
                    flush=True,
                )
            time.sleep(max(0.0, args.sleep))
        if stop_early and not all_hits:
            break

        shortlist = merge_shortlist(all_hits, tv_rows, mode=args.mode if tv_rows else "or")
        if not tv_rows:
            shortlist = merge_shortlist(all_hits, [], mode="or")

        if args.quality_buys:
            raw_buys = filter_quality_buys(
                shortlist,
                min_rvol=args.rvol_b,
                max_rvol=args.buy_max_rvol,
                min_price=args.buy_min_price,
                max_price=args.buy_max_price,
                require_range_expansion=args.require_range_expansion,
            )
        else:
            raw_buys = shortlist

        sess = session_label(cfg=session_cfg)
        window_ok = in_trade_window(cfg=session_cfg)
        regime_ok, regime_info = spy_regime_ok(
            require_green=args.require_spy_green,
            min_chg_pct=args.spy_min_chg_pct,
        )
        blocked_reason = None
        if not window_ok:
            blocked_reason = f"outside_trade_window:{sess}"
        elif not regime_ok:
            blocked_reason = f"spy_regime:{regime_info.get('reason')}"

        if blocked_reason:
            buys = []
            print(
                f"Loop {loop}: hits={len(all_hits)} shortlist={len(shortlist)} "
                f"raw_quality={len(raw_buys)} BLOCKED ({blocked_reason}) "
                f"spy={regime_info.get('chg_pct')}%",
                flush=True,
            )
        else:
            buys = rank_and_size(raw_buys, sizing=sizing_cfg, exits=exit_cfg)
            buy_tickers = [r["ticker"] for r in buys]
            print(
                f"Loop {loop}: hits={len(all_hits)} shortlist={len(shortlist)} "
                f"raw_quality={len(raw_buys)} top{sizing_cfg.top_n}={len(buys)} "
                f"{buy_tickers} sess={sess} spy={regime_info.get('chg_pct')}%",
                flush=True,
            )
            for r in buys:
                print(
                    f"  BUY#{r.get('rank')} {r['ticker']} rvol={r.get('rel_volume')} "
                    f"size×{r.get('size_mult')} px={r.get('price')} "
                    f"TP={r.get('exit_plan', {}).get('take_profit')} "
                    f"SL={r.get('exit_plan', {}).get('stop_loss')}",
                    flush=True,
                )

        buy_tickers = [r["ticker"] for r in buys]
        buys_seen.update(buy_tickers)
        loops_since_status += 1

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        now_ts = time.time()
        payload = {
            "loop": loop,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "universe_size": len(universe),
            "daytrade_hits": all_hits,
            "shortlist": shortlist,
            "raw_quality_buys": raw_buys,
            "buys": buys,
            "session": sess,
            "blocked": blocked_reason,
            "spy_regime": regime_info,
            "rules": {
                "min_rvol": args.rvol_b,
                "max_rvol": args.buy_max_rvol,
                "top_n": sizing_cfg.top_n,
                "open_window_minutes": session_cfg.open_window_minutes,
                "require_spy_green": args.require_spy_green,
                "spy_min_chg_pct": args.spy_min_chg_pct,
                "require_range_expansion": args.require_range_expansion,
                "take_profit_pct": exit_cfg.take_profit_pct,
                "stop_loss_pct": exit_cfg.stop_loss_pct,
                "time_stop_minutes": exit_cfg.time_stop_minutes,
                "confirm_minutes": exit_cfg.confirm_minutes,
            },
        }
        path = OUT_DIR / f"rotate_{stamp}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        (OUT_DIR / "rotate_latest.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        (OUT_DIR / "buys_latest.json").write_text(
            json.dumps(
                {
                    "created_at": payload["created_at"],
                    "loop": loop,
                    "session": sess,
                    "blocked": blocked_reason,
                    "spy_regime": regime_info,
                    "buys": buys,
                    "raw_quality_count": len(raw_buys),
                    "rules": payload["rules"],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        # Journal only new / pattern-changed buys (keeps continuous scan from flooding)
        to_journal_tickers: set[str] = set()
        for r in buys:
            t = r["ticker"]
            pats = tuple(sorted(r.get("patterns") or []))
            prev = journaled.get(t)
            if prev is None:
                to_journal_tickers.add(t)
            else:
                prev_pats, prev_ts = prev
                if pats != prev_pats or (now_ts - prev_ts) >= journal_cooldown_s:
                    to_journal_tickers.add(t)
        for t in to_journal_tickers:
            r = next(x for x in buys if x["ticker"] == t)
            journaled[t] = (tuple(sorted(r.get("patterns") or [])), now_ts)

        cases = build_cases(
            [h for h in all_hits if h.get("ticker") in to_journal_tickers],
            [r for r in tv_rows if r.get("ticker") in to_journal_tickers],
            run_id=f"rot{loop}-{stamp}",
            scanner_a="daytrade",
        )
        if to_journal_tickers:
            print(f"  New/changed BUYs journaled: {sorted(to_journal_tickers)}", flush=True)
        ta_results: list[dict] = []
        due_periodic = (
            validate_interval_s > 0
            and buys
            and (time.time() - last_validate_at) >= validate_interval_s
        )
        do_validate = bool(buys) and (args.validate or due_periodic)
        if do_validate:
            to_val = buys[: args.limit_validate] if args.limit_validate else buys
            why = "scheduled" if due_periodic and not args.validate else "every-loop"
            print(
                f"VALIDATING {len(to_val)} BUYs via TradingAgents ({why}): "
                f"{[r['ticker'] for r in to_val]}",
                flush=True,
            )
            rc = _validate(to_val, args.ta_workers)
            last_validate_at = time.time()
            print(f"Validation exit code={rc}", flush=True)
            # Exclude rotate_validate_input_*.json (those are shortlist payloads)
            reports = sorted(
                p
                for p in OUT_DIR.glob("rotate_validate_*.json")
                if "input" not in p.name
            )
            if reports:
                try:
                    ta_results = json.loads(reports[-1].read_text(encoding="utf-8")).get("results") or []
                except json.JSONDecodeError:
                    ta_results = []
            cases = attach_ta_results(cases, ta_results)
            for r in ta_results:
                print(
                    f"  TA {r.get('ticker')}: {r.get('action') or r.get('decision')} "
                    f"rating={r.get('rating')}",
                    flush=True,
                )
            # Persist TA verdicts for this batch (also used by later price check)
            (OUT_DIR / "ta_latest.json").write_text(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "tickers": [r["ticker"] for r in to_val],
                        "results": ta_results,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        if cases:
            journal_path = log_cases(cases)
            print(f"Journaled {len(cases)} BUY cases → {journal_path}", flush=True)

        if status_every_s > 0 and (now_ts - last_status_at) >= status_every_s:
            elapsed_m = (now_ts - started) / 60.0
            print(
                f"\n===== STATUS @ {datetime.now(timezone.utc).isoformat()} "
                f"| elapsed={elapsed_m:.1f}m loops={loop} =====",
                flush=True,
            )
            print(
                f"  session={sess} blocked={blocked_reason} "
                f"min_rvol={args.rvol_b} top_n={sizing_cfg.top_n}",
                flush=True,
            )
            print(f"  Current BUYs ({len(buys)}): {buy_tickers}", flush=True)
            print(f"  Unique BUYs seen this run: {len(buys_seen)}", flush=True)
            print(f"  Loops since last status: {loops_since_status}", flush=True)
            for r in buys:
                print(
                    f"    BUY {r['ticker']} patterns={r.get('patterns')} "
                    f"rvol={r.get('rel_volume')} px={r.get('price')}",
                    flush=True,
                )
            status = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "elapsed_minutes": round(elapsed_m, 2),
                "loop": loop,
                "session": sess,
                "blocked": blocked_reason,
                "rules": payload["rules"],
                "buys": buys,
                "unique_buys_seen": sorted(buys_seen),
                "loops_since_status": loops_since_status,
            }
            (OUT_DIR / "status_latest.json").write_text(
                json.dumps(status, indent=2, default=str), encoding="utf-8"
            )
            last_status_at = now_ts
            loops_since_status = 0

        # Rotate universe order so the same names aren't always first
        universe = universe[args.batch_size :] + universe[: args.batch_size]

        if args.loop_sleep and args.loop_sleep > 0:
            remaining = (
                deadline - time.time() if deadline is not None else args.loop_sleep
            )
            nap = min(args.loop_sleep, max(0.0, remaining))
            if nap > 0 and (deadline is None or time.time() + 1 < deadline):
                time.sleep(nap)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
