$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    $Key = & (Join-Path $PSScriptRoot "generate-api-key.ps1")
    (Get-Content ".env") -replace "^VLLM_API_KEY=.*$", "VLLM_API_KEY=$Key" |
        Set-Content ".env" -Encoding ascii
    Write-Host "Created .env with a generated API key."
} else {
    Write-Host "Using existing .env."
}

docker compose config --quiet
docker compose up -d
docker compose ps
