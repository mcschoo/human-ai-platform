$ErrorActionPreference = "Stop"
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$ScriptPath = Join-Path $PSScriptRoot "status.py"

& $Python $ScriptPath @args
exit $LASTEXITCODE
