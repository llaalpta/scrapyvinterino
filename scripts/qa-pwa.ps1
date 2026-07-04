param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "start",
    [int]$Port = 5176
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$StateDir = Join-Path $env:TEMP "scrapyvinterino-qa"
$PidFile = Join-Path $StateDir "vite-$Port.pid"
$StampFile = Join-Path $StateDir "vite-$Port.started"
$OutLog = Join-Path $StateDir "vite-$Port.out.log"
$ErrLog = Join-Path $StateDir "vite-$Port.err.log"
$FrontendUrl = "http://127.0.0.1:$Port"

function Get-RecordedVitePid {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return $null
    }
    $raw = Get-Content -LiteralPath $PidFile -Raw
    $parsed = 0
    if ([int]::TryParse($raw.Trim(), [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function Stop-RecordedVite {
    $pidValue = Get-RecordedVitePid
    if ($null -ne $pidValue) {
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            $startedAt = if (Test-Path -LiteralPath $StampFile) { [datetime](Get-Content -LiteralPath $StampFile -Raw) } else { $null }
            if ($null -eq $startedAt -or $process.StartTime -lt $startedAt.AddSeconds(-2)) {
                throw "Recorded PID $pidValue does not match the tracked QA Vite process. Refusing to stop it."
            }
            taskkill.exe /PID $pidValue /T /F | Out-Null
        }
    }
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $StampFile -ErrorAction SilentlyContinue
}

function Assert-PortAvailableOrOwned {
    $connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($connections.Count -eq 0) {
        return
    }

    $recordedPid = Get-RecordedVitePid
    $foreignConnections = @($connections | Where-Object { $null -eq $recordedPid -or $_.OwningProcess -ne $recordedPid })
    if ($foreignConnections.Count -gt 0) {
        $owners = ($foreignConnections | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
        throw "Port $Port is already used by process id(s): $owners. Stop that process or choose another -Port."
    }

    Stop-RecordedVite
}

function Wait-HttpOk([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Url"
}

if ($Action -eq "stop") {
    Stop-RecordedVite
    Write-Host "Stopped tracked Vite process for $FrontendUrl"
    exit 0
}

if ($Action -eq "status") {
    $pidValue = Get-RecordedVitePid
    $viteState = if ($null -ne $pidValue -and $null -ne (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) { "running pid $pidValue" } else { "not running" }
    Write-Host "Frontend: $viteState at $FrontendUrl"
    Push-Location $RepoRoot
    try {
        docker compose ps
    } finally {
        Pop-Location
    }
    exit 0
}

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
Assert-PortAvailableOrOwned

Push-Location $RepoRoot
try {
    docker compose up -d postgres redis api worker
    Wait-HttpOk "http://localhost:8000/health" 60
} finally {
    Pop-Location
}

$command = "set VITE_DEV_API_PROXY_TARGET=http://localhost:8000&& pnpm.cmd dev --host 127.0.0.1 --port $Port"
$process = Start-Process `
    -FilePath "cmd.exe" `
    -ArgumentList "/c `"$command`"" `
    -WorkingDirectory (Join-Path $RepoRoot "frontend") `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $StampFile -Value $process.StartTime.ToString("o")
Set-Content -LiteralPath $PidFile -Value $process.Id
try {
    Wait-HttpOk $FrontendUrl 45
} catch {
    Stop-RecordedVite
    throw
}

Write-Host "QA frontend ready: $FrontendUrl"
Write-Host "API health: http://localhost:8000/health"
Write-Host "Logs: $OutLog / $ErrLog"
