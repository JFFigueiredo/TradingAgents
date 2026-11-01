# Meme Desk (PAPER ONLY)

Folder name is historical (`solana_desk/`); the desk now papers **Solana + Base + Ethereum + BSC + Arbitrum**.  
Fully separate from stock `pipeline/`. **No wallet. No private keys. No real orders.**

Starting paper balance: **$280 USD** per config.

## Quick start (Windows)

```powershell
cd d:\Documentos\Projects\TradingAgents\solana_desk
python -m pip install -r requirements.txt

python run.py --config control --headless
powershell -File .\watchdog.ps1 -ConfigId control
```

## Chains & safety

| Chain | Discovery | Safety (fail closed) |
|-------|-----------|----------------------|
| Solana | GeckoTerminal + DexScreener | RugCheck + Solana RPC mint/freeze |
| Base / ETH / BSC / Arbitrum | GeckoTerminal + DexScreener | GoPlus token security (honeypot, mint, LP lock, taxes, holders) |

## Variants lab (large grid)

**Do not edit** `configs/control.json`. Old variants kept: `higher_volume`, `tighter_stops`, `momentum_bias`, `deep_liquidity`.

Chains: `solana`, `base`, `ethereum`, `bsc`, `arbitrum`, `polygon`, `avalanche`, `optimism`, `blast`, `linea`.

See full list: `configs/variants_catalog.json`.

Launch a batch:

```powershell
.\run_lab.ps1 -Preset core -Reset
.\run_lab.ps1 -Preset chains -Reset
```

```bash
PRESET=core RESET=1 ./run_lab.sh
PRESET=chains RESET=1 ./run_lab.sh
```

Portable prompt for another server: `SERVER_PROMPT.md`.

### Solana experiments (new)

`winner_like_v1`, `mid_age_sweet`, `young_only`, `buy_pressure`, `low_concentration`, `score_strict`, `score_loose`, `scalp_fast`, `wide_trail`, `size_up_few`, `anti_overshoot`, `raydium_only`, `pumpswap_graduated`

### Other chains (new)

`base_control`, `base_momentum`, `base_deep_liq`, `winner_like_base`, `eth_control`, `eth_strict`, `bsc_control`, `bsc_anti_tax`, `arb_control`, `multi_l2`, `multi_sol_base`, `multi_all`

### Auto from winners

```powershell
python -m desk.winner_analysis          # leaderboard + hints
python -m desk.propose_variants         # writes configs/variants/auto/auto_YYYYMMDD.json
python run.py --config auto_20261101 --headless --reset
```

Current open control fills already shaped `auto_20261101` and `winner_like_v1`: ~$98k liq, ~$257k 1h vol, ~7% top10, ~17h age.

## Run several in parallel

Each `--config` gets its own `data/<id>/` state — safe to run many:

```powershell
python run.py --config winner_like_v1 --headless --reset
python run.py --config base_control --headless --reset
python run.py --config eth_control --headless --reset
python run.py --config multi_sol_base --headless --reset
```

Regenerate the hand-written experiment grid (won't overwrite the 4 locked originals):

```powershell
python scripts/generate_variants.py
```

## Architecture

1. **Scout** — per-chain Gecko new pools + dex pools + DexScreener profiles  
2. **Filter** — fail closed; soft/hard rejects; budgeted heavy checks  
3. **Analyst** — weighted score  
4. **Executor** — paper fills (slippage, fee, impact)  
5. **Risk** — reprice ~5s; stops / trail / max hold / liquidity rug  

## Safety

- Paper only — no signing or order submission path.
- APIs 429 often; desk backs off and still manages open positions.
- Paper fills are optimistic vs real MEV / failed txs / latency.
