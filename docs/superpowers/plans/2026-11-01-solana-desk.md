# Solana Paper Trading Desk Implementation Plan

> **For agentic workers:** Implement task-by-task in `solana_desk/` only. Do not touch `pipeline/` or TradingAgents core.

**Goal:** Build a PAPER-ONLY Solana memecoin desk (control baseline + experiment variants) in a fully separate folder.

**Architecture:** Long-lived Python process with Scout → Filter → Analyst → Executor → Risk loop; atomic `state.json`; Rich/text dashboard; free public APIs only.

**Tech Stack:** Python 3.10+, `requests`, `rich` (optional dashboard), no wallet/signing libs.

**Spec:** User control prompt (frozen rules in `configs/control.json`).

## Global Constraints

- Paper trading only: no wallet, keys, or real orders
- Fail closed on unverifiable checks
- Separate folder: `solana_desk/`
- Control config never auto-tuned; variants are separate configs

## Tasks

1. Scaffold package, requirements, README, gitignore for runtime data
2. HTTP client with timeout/retry/backoff; API adapters
3. Filter + Analyst (exact control formulas)
4. Executor + Risk (sizing, fees, exits)
5. Persistence, logging, dashboard, CLI (`--headless`, `--reset`, `--config`)
6. Watchdog (bash + PowerShell)
7. Variant configs + winner analysis script
