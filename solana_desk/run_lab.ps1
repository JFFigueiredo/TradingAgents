# Launch a batch of paper variants (each gets its own data/<id>/ and process).
# Usage:
#   .\run_lab.ps1                         # default core set
#   .\run_lab.ps1 -Preset all             # everything in variants_catalog (heavy)
#   .\run_lab.ps1 -Preset chains          # one control per chain
#   .\run_lab.ps1 -Configs control,base_control,winner_like_v1
#   .\run_lab.ps1 -Reset                  # wipe those configs before start
param(
    [ValidateSet("core", "chains", "solana", "multi", "all")]
    [string]$Preset = "core",
    [string]$Configs = "",
    [string]$Python = "python",
    [switch]$Reset,
    [switch]$WithWatchdog
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$core = @(
    "control", "winner_like_v1", "anti_overshoot", "deep_liquidity",
    "base_control", "bsc_control", "eth_control", "multi_sol_base", "auto_20261101"
)
$chains = @(
    "control", "base_control", "eth_control", "bsc_control", "arb_control",
    "polygon_control", "avalanche_control", "optimism_control", "blast_control", "linea_control"
)
$solana = @(
    "control", "higher_volume", "tighter_stops", "momentum_bias", "deep_liquidity",
    "winner_like_v1", "mid_age_sweet", "scalp_fast", "wide_trail", "anti_overshoot",
    "sol_stop5_trail10", "sol_score_thr68", "sol_size10x3"
)
$multi = @("multi_sol_base", "multi_l2", "multi_meme_hot", "multi_l2_plus", "multi_alt_evm", "multi_universe")

if ($Configs) {
    $list = $Configs.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
} elseif ($Preset -eq "chains") {
    $list = $chains
} elseif ($Preset -eq "solana") {
    $list = $solana
} elseif ($Preset -eq "multi") {
    $list = $multi
} elseif ($Preset -eq "all") {
    $cat = Get-Content (Join-Path $Root "configs\variants_catalog.json") -Raw | ConvertFrom-Json
    $list = $cat | ForEach-Object { $_.id }
} else {
    $list = $core
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data\lab_logs") | Out-Null
$started = @()

foreach ($id in $list) {
    $args = @("run.py", "--config", $id, "--headless")
    if ($Reset) { $args += "--reset" }
    $out = Join-Path $Root "data\lab_logs\$id.out.log"
    $err = Join-Path $Root "data\lab_logs\$id.err.log"
    $proc = Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $Root `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    $started += [pscustomobject]@{ config = $id; pid = $proc.Id }
    Write-Host "started $id pid=$($proc.Id)"
    if ($WithWatchdog) {
        Start-Process -FilePath "powershell" -ArgumentList @(
            "-File", (Join-Path $Root "watchdog.ps1"), "-ConfigId", $id, "-Python", $Python
        ) -WindowStyle Hidden | Out-Null
    }
    Start-Sleep -Milliseconds 400
}

$started | ConvertTo-Json | Set-Content (Join-Path $Root "data\lab_logs\started.json")
Write-Host "Launched $($started.Count) desks. Leaderboard later: python -m desk.winner_analysis"
