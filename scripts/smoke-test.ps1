$ErrorActionPreference = "Stop"
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$Root = Split-Path -Parent $PSScriptRoot

& $Python (Join-Path $PSScriptRoot "status.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:HAI_LIVE_TESTS = "1"
& $Python -m pytest (Join-Path $Root "tests") -m live @args
exit $LASTEXITCODE
