$ErrorActionPreference = "Stop"
$dest = Join-Path $PSScriptRoot "vendor\stock-pattern"
if (Test-Path $dest) {
  Write-Host "stock-pattern already present at $dest"
  exit 0
}
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "vendor") | Out-Null
git clone --depth 1 https://github.com/BennyThadikaran/stock-pattern.git $dest
Write-Host "Cloned stock-pattern into $dest"
