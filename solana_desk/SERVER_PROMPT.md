# Copy-paste prompt for a **remote** server / coding assistant

Paste everything below the line on the remote machine’s assistant (or run the commands yourself).

Repo: `https://github.com/JFFigueiredo/TradingAgents.git`  
Desk path after clone: `TradingAgents/solana_desk/`

---

```
You are setting up and running a PAPER-ONLY multi-chain memecoin trading desk on this REMOTE server.

CRITICAL RULES
- Paper trading only. No wallets, no private keys, no real orders, no signing.
- Do not add live-trading capability.
- Work only inside TradingAgents/solana_desk/ after cloning.

REPO
- GitHub: https://github.com/JFFigueiredo/TradingAgents.git
- Branch: prefer `feat/solana-meme-desk` if it exists; otherwise `main`.
- The desk lives at: solana_desk/ (folder name is historical; it supports many chains).

WHAT THIS IS
A long-lived Python 3 process that:
1) Scouts new pools (GeckoTerminal + DexScreener)
2) Filters risk (Solana: RugCheck+RPC; EVM: GoPlus)
3) Scores survivors and paper-buys
4) Reprices every ~5s and exits on hard rules
5) Saves state to data/<config_id>/state.json

Chains: solana, base, ethereum, bsc, arbitrum, polygon, avalanche, optimism, blast, linea.

SETUP ON THIS SERVER
1. Install git + Python 3.10+ if missing.
2. Clone (or pull) the repo:
     git clone https://github.com/JFFigueiredo/TradingAgents.git
     cd TradingAgents
     git fetch origin
     git checkout feat/solana-meme-desk 2>/dev/null || git checkout main
     git pull
3. cd solana_desk
4. python3 -m venv .venv && source .venv/bin/activate
   # Windows: py -3 -m venv .venv ; .\.venv\Scripts\Activate.ps1
5. pip install -r requirements.txt
6. python scripts/generate_variants.py
7. python tests/test_analyst.py

RUN ONE DESK
  python run.py --config control --headless
  python run.py --config control --headless --reset   # wipe that config’s paper book only

RUN A PROFIT LAB (many variants in parallel)
Linux/macOS:
  chmod +x run_lab.sh watchdog.sh
  PRESET=core RESET=1 WATCHDOG=1 ./run_lab.sh
  PRESET=chains RESET=1 ./run_lab.sh

Windows PowerShell:
  .\run_lab.ps1 -Preset core -Reset -WithWatchdog
  .\run_lab.ps1 -Preset chains -Reset

Recommended first remote batch:
  cd solana_desk
  CONFIGS=control,winner_like_v1,anti_overshoot,deep_liquidity,base_control,bsc_control,eth_control,polygon_control,avalanche_control,multi_sol_base,multi_meme_hot
  RESET=1 WATCHDOG=1 CONFIGS="$CONFIGS" ./run_lab.sh

Keep the session alive with tmux/screen, or rely on watchdog + nohup/setsid (already used by run_lab.sh / watchdog.sh).

WATCHDOG
- Linux: CONFIG_ID=<id> bash watchdog.sh
- Windows: powershell -File .\watchdog.ps1 -ConfigId <id>
Never pass --reset from the watchdog (resume state after crash).

MEASURE WHAT WINS
  python -m desk.winner_analysis
  python -m desk.propose_variants
  python run.py --config auto_YYYYMMDD --headless --reset

OPERATIONS NOTES
- Free APIs rate-limit (429). Backoff is built in; quiet days = few filter passes.
- Each --config is isolated under data/<config_id>/.
- Leave configs/control.json untuned as the baseline.
- Paper PnL is optimistic vs real trading (latency, failed txs, MEV).
- Do not commit data/, .venv/, or *.log back to git.

DO NEXT ON THIS SERVER
1. Clone/checkout the branch and install deps.
2. Launch the recommended first batch with watchdogs (under tmux if SSH).
3. After several hours / many closed trades, run winner_analysis + propose_variants.
4. Start the top 3 leaderboard configs plus the new auto_* variant.
5. Reply with the leaderboard: equity, win/loss, stop overshoots, per config id.
```
