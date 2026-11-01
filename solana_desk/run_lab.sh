#!/usr/bin/env bash
# Launch a batch of paper variants.
# Usage:
#   ./run_lab.sh                  # core preset
#   PRESET=chains ./run_lab.sh
#   PRESET=all RESET=1 ./run_lab.sh
#   CONFIGS=control,base_control ./run_lab.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
PRESET="${PRESET:-core}"
RESET="${RESET:-0}"
WATCHDOG="${WATCHDOG:-0}"
mkdir -p data/lab_logs

core="control winner_like_v1 anti_overshoot deep_liquidity base_control bsc_control eth_control multi_sol_base"
chains="control base_control eth_control bsc_control arb_control polygon_control avalanche_control optimism_control blast_control linea_control"
solana="control higher_volume tighter_stops momentum_bias deep_liquidity winner_like_v1 mid_age_sweet scalp_fast wide_trail anti_overshoot"
multi="multi_sol_base multi_l2 multi_meme_hot multi_l2_plus multi_alt_evm multi_universe"

if [[ -n "${CONFIGS:-}" ]]; then
  # shellcheck disable=SC2206
  list=(${CONFIGS//,/ })
elif [[ "$PRESET" == "chains" ]]; then
  # shellcheck disable=SC2206
  list=($chains)
elif [[ "$PRESET" == "solana" ]]; then
  list=($solana)
elif [[ "$PRESET" == "multi" ]]; then
  list=($multi)
elif [[ "$PRESET" == "all" ]]; then
  mapfile -t list < <("$PYTHON" -c "import json; print('\n'.join(x['id'] for x in json.load(open('configs/variants_catalog.json'))))")
else
  list=($core)
fi

: > data/lab_logs/started.txt
for id in "${list[@]}"; do
  [[ -z "$id" ]] && continue
  args=(run.py --config "$id" --headless)
  if [[ "$RESET" == "1" ]]; then args+=(--reset); fi
  nohup setsid "$PYTHON" "${args[@]}" >>"data/lab_logs/${id}.out.log" 2>>"data/lab_logs/${id}.err.log" &
  echo "$id $!" | tee -a data/lab_logs/started.txt
  if [[ "$WATCHDOG" == "1" ]]; then
    CONFIG_ID="$id" nohup bash watchdog.sh >>"data/lab_logs/${id}.watchdog.log" 2>&1 &
  fi
  sleep 0.4
done
echo "Launched ${#list[@]} desks. Later: $PYTHON -m desk.winner_analysis"
