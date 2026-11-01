# PowerShell watchdog for Windows — equivalent of watchdog.sh
# Usage: .\watchdog.ps1 -ConfigId control
param(
    [string]$ConfigId = "control",
    [string]$Python = "python"
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root "data\$ConfigId"
$Log = Join-Path $DataDir "desk.log"
$PidFile = Join-Path $DataDir "desk.pid"
$LockFile = Join-Path $DataDir "watchdog.lock"
$OutLog = Join-Path $DataDir "watchdog.out"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# Simple lock via exclusive file create
try {
    $lockStream = [System.IO.File]::Open($LockFile, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    Write-Host "watchdog already running for $ConfigId"
    exit 0
}

function Write-Watch([string]$msg) {
    $line = "{0} {1}" -f (Get-Date).ToUniversalTime().ToString("o"), $msg
    Add-Content -Path $OutLog -Value $line
}

function Get-DeskPid {
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $raw) { return $null }
    try { return [int]$raw } catch { return $null }
}

function Test-DeskRunning {
    $deskPid = Get-DeskPid
    if (-not $deskPid) { return $false }
    try {
        Get-Process -Id $deskPid -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Test-LogFresh {
    if (-not (Test-Path $Log)) { return $false }
    $age = ([DateTime]::UtcNow - (Get-Item $Log).LastWriteTimeUtc).TotalSeconds
    return ($age -lt 180)
}

function Start-Desk {
    Set-Location $Root
    $proc = Start-Process -FilePath $Python -ArgumentList @("run.py", "--config", $ConfigId, "--headless") `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $DataDir "desk.stdout.log") `
        -RedirectStandardError (Join-Path $DataDir "desk.stderr.log")
    Set-Content -Path $PidFile -Value $proc.Id
    Write-Watch "launched desk pid=$($proc.Id)"
}

Write-Watch "watchdog start config=$ConfigId"

try {
    while ($true) {
        $running = Test-DeskRunning
        $fresh = Test-LogFresh
        if (-not $running -or -not $fresh) {
            if ($running) {
                $deskPid = Get-DeskPid
                Write-Watch "freezing — stop pid=$deskPid"
                try { Stop-Process -Id $deskPid -Force -ErrorAction SilentlyContinue } catch {}
                Start-Sleep -Seconds 5
                try { Stop-Process -Id $deskPid -Force -ErrorAction SilentlyContinue } catch {}
            }
            Start-Desk
        }
        Start-Sleep -Seconds 60
    }
}
finally {
    $lockStream.Close()
    $lockStream.Dispose()
}
