# Cold start for Windows / PowerShell.
#
#   .\scripts\bootstrap.ps1            # 6 hours of history
#   .\scripts\bootstrap.ps1 -Hours 24
#
# Safe to re-run: the backfill upserts, so windows are updated, not duplicated.

param(
    [double]$Hours = 6
)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Ok($message) { Write-Host "    $message" -ForegroundColor Green }

docker info *> $null
if (-not $?) {
    Write-Host "Docker is not running. Start Docker Desktop and try again." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path '.env')) {
    Write-Step 'Creating .env from .env.example'
    Copy-Item '.env.example' '.env'
}

Write-Step 'Building images (first run pulls Spark and Kafka; expect a few minutes)'
docker compose build
if (-not $?) { exit 1 }

Write-Step 'Starting the platform'
docker compose up -d
if (-not $?) { exit 1 }

Write-Step 'Waiting for the API'
$ready = $false
foreach ($attempt in 1..60) {
    try {
        Invoke-RestMethod -Uri 'http://localhost:8000/api/health' -TimeoutSec 4 | Out-Null
        $ready = $true
        Write-Ok 'API is up'
        break
    } catch {
        Start-Sleep -Seconds 3
    }
}
if (-not $ready) {
    Write-Host 'API did not come up in time — check: docker compose logs api' -ForegroundColor Red
    exit 1
}

Write-Step "Backfilling $Hours h of history (metrics, sessions, incidents)"
$env:BACKFILL_HOURS = $Hours
docker compose --profile seed run --rm backfill

Write-Step 'Training the anomaly models on that history'
docker compose --profile seed run --rm train

Write-Step 'Waiting for the streaming pipeline to produce a live window'
foreach ($attempt in 1..40) {
    try {
        $health = Invoke-RestMethod -Uri 'http://localhost:8000/api/system/health' -TimeoutSec 5
        if ($null -ne $health.pipeline_lag_seconds -and $health.pipeline_lag_seconds -lt 240) {
            Write-Ok "pipeline is live (lag $($health.pipeline_lag_seconds)s)"
            break
        }
    } catch { }
    Start-Sleep -Seconds 5
}

Write-Host ''
Write-Host 'LogLens is ready.' -ForegroundColor Green
Write-Host '  Dashboard   http://localhost:3000'
Write-Host '  API docs    http://localhost:8000/docs'
Write-Host '  Grafana     http://localhost:3001   (admin / loglens)'
Write-Host '  Spark UI    http://localhost:9090'
Write-Host ''
Write-Host 'Try it: click Simulate in the top bar, or run'
Write-Host '  Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/system/scenarios -ContentType application/json -Body ''{"type":"ddos_burst"}'''
