"""Long-lived paper desk loop."""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import traceback
from pathlib import Path

# allow `python -m desk.main` from solana_desk/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desk.config import load_config
from desk.dashboard import print_dashboard, render_text
from desk.http_client import HttpClient
from desk.logging_util import DeskLogger
from desk.state import bump, equity_usd, load_state, save_state
from desk.stages import analyst, filter as filt
from desk.stages import executor, risk, scout


def data_dir_for(cfg: dict) -> Path:
    cid = str(cfg.get("id") or "control")
    return ROOT / "data" / cid


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Paper-only multi-chain memecoin desk (Solana/Base/ETH/BSC/Arb)")
    p.add_argument("--config", default="control", help="config id or path (default: control)")
    p.add_argument("--headless", action="store_true", help="no Rich dashboard spam")
    p.add_argument("--reset", action="store_true", help="wipe state and start at starting cash")
    p.add_argument("--once", action="store_true", help="one scout cycle then exit (debug)")
    return p.parse_args(argv)


def reset_data(ddir: Path) -> None:
    if ddir.exists():
        shutil.rmtree(ddir)
    ddir.mkdir(parents=True, exist_ok=True)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config)
    ddir = data_dir_for(cfg)
    if args.reset:
        reset_data(ddir)
    ddir.mkdir(parents=True, exist_ok=True)

    state_path = ddir / "state.json"
    desk_log = DeskLogger(ddir)
    start_cash = float(cfg.get("starting_cash_usd", 280.0))
    state = load_state(state_path, start_cash, str(cfg.get("id") or "control"))
    # if resuming under different config id, keep money but note it
    state["config_id"] = cfg.get("id") or state.get("config_id")
    save_state(state_path, state)

    http = HttpClient(
        timeout_sec=float(cfg.get("http_timeout_sec", 12)),
        max_retries=int(cfg.get("http_max_retries", 3)),
    )

    desk_log.info(
        f"desk start config={cfg.get('id')} cash=${state['cash_usd']:.2f} "
        f"equity=${equity_usd(state):.2f} headless={args.headless} reset={args.reset}"
    )
    desk_log.info("PAPER TRADING ONLY — no wallet, no keys, no real orders")

    scout_every = float(cfg.get("scout_interval_sec", 30))
    reprice_every = float(cfg.get("reprice_interval_sec", 5))
    last_scout = 0.0
    last_dash = 0.0
    last_reprice = [0.0]

    def manage_positions() -> None:
        if state.get("positions"):
            risk.reprice_and_manage(http, cfg, state, state_path, desk_log)
            save_state(state_path, state)
        last_reprice[0] = time.time()

    def tick() -> None:
        # Scout + filter can take minutes under 429 backoff; keep stops live meanwhile.
        if state.get("positions") and (time.time() - last_reprice[0]) >= reprice_every:
            try:
                manage_positions()
            except Exception as exc:  # noqa: BLE001
                desk_log.error(f"in-scout reprice error (continuing): {exc}")

    on_tick = tick if cfg.get("reprice_during_scout") else None

    while True:
        loop_start = time.time()
        try:
            # Risk first: never stop managing open positions
            manage_positions()

            if (time.time() - last_scout) >= scout_every:
                last_scout = time.time()
                try:
                    candidates = scout.run_scout(http, cfg, on_tick=on_tick)
                    bump(state, "pairs_scanned", len(candidates))
                    desk_log.info(f"scout candidates={len(candidates)}")
                    passed = filt.run_filter(http, cfg, state, candidates, on_tick=on_tick)
                    scored = analyst.run_analyst(cfg, state, passed)
                    executor.try_entries(cfg, state, scored, state_path, desk_log)
                    save_state(state_path, state)
                except Exception as exc:  # noqa: BLE001
                    desk_log.error(f"scout cycle error (continuing): {exc}")
                    desk_log.event("scout_error", str(exc), traceback=traceback.format_exc())

            if not args.headless and (time.time() - last_dash) >= 5.0:
                print_dashboard(state, cfg, headless=False)
                last_dash = time.time()
            elif args.headless and (time.time() - last_dash) >= 60.0:
                # heartbeat into log
                desk_log.info(render_text(state, cfg).splitlines()[1])
                last_dash = time.time()

            if args.once:
                desk_log.info(" --once complete")
                return 0

        except KeyboardInterrupt:
            desk_log.info("shutdown requested")
            save_state(state_path, state)
            return 0
        except Exception as exc:  # noqa: BLE001
            desk_log.error(f"main loop error (continuing): {exc}")
            desk_log.event("loop_error", str(exc), traceback=traceback.format_exc())
            # still try to manage positions next iteration

        elapsed = time.time() - loop_start
        sleep_for = max(0.2, min(reprice_every, reprice_every - elapsed))
        time.sleep(sleep_for)


if __name__ == "__main__":
    raise SystemExit(run())
