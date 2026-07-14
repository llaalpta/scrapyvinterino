param(
    [ValidateSet("identity", "catalog-fail-stop", "prepared-session-read-model", "full")]
    [string]$Scenario = "identity",

    [ValidateRange(1, 3)]
    [int]$Repeat = 2
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$FrontendDir = Join-Path $RepoRoot "frontend"
$TestsDirectory = [IO.Path]::GetFullPath((Join-Path $BackendDir "tests"))
$TestsRoot = $TestsDirectory + [IO.Path]::DirectorySeparatorChar
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$ComposeProject = (Split-Path -Leaf $RepoRoot).ToLowerInvariant()
$RedisDatabase = 15
$RedisLeaseKey = "qa:isolated-integration:lease"
$TestTargets = @{
    "identity" = @(
        "tests/test_proxy_identity_fence.py::test_real_scheduler_producer_and_consumer_loop_preserve_stale_identity_fence"
    )
    "catalog-fail-stop" = @(
        "tests/test_catalog_failstop_integration.py::test_catalog_terminal_response_fails_once_invalidates_session_and_acks",
        "tests/test_manual_runs.py::test_datadome_mid_batch_rolls_back_and_queues_every_claimed_candidate",
        "tests/test_item_detail_state_audit.py::test_transient_failure_while_preserving_challenge_keeps_terminal_run_and_retry",
        "tests/test_item_detail_state_audit.py::test_challenge_attempt_counter_only_advances_for_failing_candidate"
    )
    "prepared-session-read-model" = @(
        "tests/test_prepared_session_read_model.py",
        "tests/test_prepared_session_live_contract.py::test_live_prepared_session_read_model_matches_runtime_and_pwa"
    )
    "full" = @("tests")
}
$ScenarioTargets = @($TestTargets[$Scenario])

function Get-RunningComposeContainers([string]$Service) {
    $Arguments = @(
        "ps",
        "--filter", "label=com.docker.compose.project=$ComposeProject",
        "--filter", "label=com.docker.compose.service=$Service",
        "--format", "{{.ID}}"
    )

    $Output = @(& docker @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the Docker service '$Service'."
    }
    $ContainerIds = @($Output | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    return $ContainerIds
}

function Assert-ExecutorServicesStopped {
    foreach ($Service in @("worker", "scheduler-watchdog")) {
        if (@(Get-RunningComposeContainers $Service).Count -gt 0) {
            throw "Docker service '$Service' is running. Stop it deliberately before isolated integration QA."
        }
    }
}

function Assert-TcpPortAvailable([int]$Port) {
    $Listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($Listeners.Count -gt 0) {
        $Owners = ($Listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
        throw "QA port $Port is already used by process id(s): $Owners. No process was stopped."
    }
}

function Wait-HttpReady([string]$Url, [int]$Seconds, [System.Diagnostics.Process]$Process) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if ($Process.HasExited) {
            throw "The isolated process for $Url exited with code $($Process.ExitCode)."
        }
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for isolated endpoint $Url."
}

function Stop-OwnedProcessTree([System.Diagnostics.Process]$Process) {
    if ($null -eq $Process -or $Process.HasExited) {
        return
    }
    & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0 -and -not $Process.HasExited) {
        throw "Could not stop owned QA process $($Process.Id)."
    }
    if (-not $Process.WaitForExit(10000)) {
        throw "Owned QA process $($Process.Id) did not exit in time."
    }
}

function Assert-ContainerBelongsToRepo([string]$Container, [string]$Service) {
    $Output = @(& docker inspect --format '{{json .Config.Labels}}' $Container 2>&1)
    if ($LASTEXITCODE -ne 0 -or $Output.Count -ne 1) {
        throw "Could not verify the working directory of Docker service '$Service'."
    }
    try {
        $Labels = ([string]$Output[0]) | ConvertFrom-Json
        $WorkingDirectory = $Labels.'com.docker.compose.project.working_dir'
        $ContainerRepoRoot = [IO.Path]::GetFullPath($WorkingDirectory)
    } catch {
        throw "Docker service '$Service' has invalid Compose labels."
    }
    if (-not $ContainerRepoRoot.Equals([IO.Path]::GetFullPath($RepoRoot), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Docker service '$Service' belongs to another repository checkout."
    }
}

function Invoke-PostgresAdmin([string]$Sql) {
    $Output = @($Sql | & docker exec -i $script:PostgresContainer sh -c 'psql -X -v ON_ERROR_STOP=1 -Atq -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "A PostgreSQL QA administration command failed."
    }
    return $Output
}

function Get-RedisDatabaseSize([int]$Database) {
    $Output = @(& docker exec $script:RedisContainer redis-cli -n $Database --raw DBSIZE 2>&1)
    if ($LASTEXITCODE -ne 0 -or $Output.Count -eq 0) {
        throw "Could not read Redis database $Database."
    }
    $Value = 0
    if (-not [int]::TryParse(([string]$Output[-1]).Trim(), [ref]$Value)) {
        throw "Redis database $Database returned an invalid size."
    }
    return $Value
}

function Acquire-RedisLease([string]$Token) {
    $Output = @(& docker exec $script:RedisContainer redis-cli -n $RedisDatabase --raw SET $RedisLeaseKey $Token NX EX 3600 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not reserve Redis database $RedisDatabase."
    }
    if ($Output.Count -eq 0 -or ([string]$Output[-1]).Trim() -ne "OK") {
        throw "Redis database $RedisDatabase is already reserved; no data was removed."
    }
    try {
        if ((Get-RedisDatabaseSize $RedisDatabase) -ne 1) {
            throw "Redis database $RedisDatabase already contains data; no existing data was removed."
        }
    } catch {
        $ValidationError = $_
        try {
            Remove-RedisLease $Token | Out-Null
        } catch {
            throw "Redis database $RedisDatabase validation failed and its QA lease could not be released."
        }
        throw $ValidationError
    }
}

function Test-RedisLeaseOwned([string]$Token) {
    $Output = @(& docker exec $script:RedisContainer redis-cli -n $RedisDatabase --raw GET $RedisLeaseKey 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not verify the Redis QA lease."
    }
    return $Output.Count -gt 0 -and ([string]$Output[-1]).Trim() -eq $Token
}

function Remove-RedisLease([string]$Token) {
    $Script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
    $Output = @(& docker exec $script:RedisContainer redis-cli -n $RedisDatabase --raw EVAL $Script 1 $RedisLeaseKey $Token 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not release the Redis QA lease."
    }
    return $Output
}

function Clear-OwnedRedisDatabase([string]$Token) {
    if (-not (Test-RedisLeaseOwned $Token)) {
        throw "The Redis QA lease is no longer owned; refusing to flush database $RedisDatabase."
    }
    & docker exec $script:RedisContainer redis-cli -n $RedisDatabase FLUSHDB | Out-Null
    if ($LASTEXITCODE -ne 0 -or (Get-RedisDatabaseSize $RedisDatabase) -ne 0) {
        throw "Redis database $RedisDatabase cleanup failed."
    }
}

function Invoke-ContainerShell([string]$Container, [string]$Command) {
    $EncodedCommand = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Command))
    $Launcher = "echo $EncodedCommand | base64 -d | sh"
    $Output = @(& docker exec $Container sh -c $Launcher 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "A read-only container snapshot command failed."
    }
    return $Output
}

function Get-OperationalPostgresDigest {
    $Command = 'set -o pipefail; pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --no-owner --no-privileges | sed "/^\\\\restrict /d; /^\\\\unrestrict /d" | sha256sum | cut -d" " -f1;'
    $Output = @(Invoke-ContainerShell $script:PostgresContainer $Command)
    if ($Output.Count -eq 0) {
        throw "Could not fingerprint the operational PostgreSQL database."
    }
    $Digest = ([string]$Output[-1]).Trim()
    if ($Digest -notmatch '^[0-9a-f]{64}$') {
        throw "The operational PostgreSQL fingerprint is invalid."
    }
    return $Digest
}

function Get-OperationalRedisDigest {
    $Command = 'set -o pipefail; redis-cli -n 0 --scan | sort | while IFS= read -r key; do printf "%s\\0" "$key"; redis-cli -n 0 --raw DUMP "$key"; done | sha256sum | cut -d" " -f1;'
    $Output = @(Invoke-ContainerShell $script:RedisContainer $Command)
    if ($Output.Count -eq 0) {
        throw "Could not fingerprint operational Redis database 0."
    }
    $Digest = ([string]$Output[-1]).Trim()
    if ($Digest -notmatch '^[0-9a-f]{64}$') {
        throw "The operational Redis fingerprint is invalid."
    }
    return $Digest
}

function Enter-IsolatedEnvironment([string]$DatabaseUrl) {
    $Pattern = '^(APP_|DATABASE_URL$|BACKEND_CORS_ORIGINS$|LOCAL_AUTH_|REDIS_URL$|SEEN_|VINTED_|WORKER_|CURL_|HUMAN_|DATADOME_|PROXY_|EGRESS_|SCHEDULER_|LOG_LEVEL$|ACTION_REQUESTS_|PYTHONPATH$|PYTEST_|ALEMBIC_|PREPARED_SESSION_QA_|VITE_DEV_API_PROXY_TARGET$|HTTP_PROXY$|HTTPS_PROXY$|ALL_PROXY$|NO_PROXY$)'
    $Saved = @{}
    $Entries = @(Get-ChildItem Env: | Where-Object { $_.Name -match $Pattern })
    foreach ($Entry in $Entries) {
        $Saved[$Entry.Name] = $Entry.Value
    }
    try {
        foreach ($Entry in $Entries) {
            [Environment]::SetEnvironmentVariable($Entry.Name, $null, "Process")
        }

        $Values = @{
            APP_ENV = "test"
            APP_SECRET_KEY = ([Guid]::NewGuid().ToString("N") + [Guid]::NewGuid().ToString("N"))
            DATABASE_URL = $DatabaseUrl
            REDIS_URL = "redis://127.0.0.1:6379/$RedisDatabase"
            PYTHONPATH = (Join-Path $BackendDir "src")
            BACKEND_CORS_ORIGINS = "http://127.0.0.1:5176"
            HTTP_PROXY = "http://127.0.0.1:9"
            HTTPS_PROXY = "http://127.0.0.1:9"
            ALL_PROXY = "http://127.0.0.1:9"
            NO_PROXY = "127.0.0.1,localhost,::1"
        }
        if ($Scenario -ne "full") {
            $Values["VINTED_BASE_URL"] = "http://127.0.0.1:9"
            $Values["VINTED_DATADOME_COLLECTOR_URL"] = "http://127.0.0.1:9"
            $Values["EGRESS_DIAGNOSTIC_URL"] = "http://127.0.0.1:9"
            $Values["VINTED_DIRECT_CATALOG_ENABLED"] = "false"
            $Values["VINTED_DATADOME_COLLECTOR_ENABLED"] = "false"
            $Values["VINTED_AUTH_ENABLED"] = "false"
            $Values["ACTION_REQUESTS_ENABLED"] = "false"
            $Values["SCHEDULER_ENABLED"] = "false"
            $Values["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        }
        if ($Scenario -eq "prepared-session-read-model") {
            $Values["PREPARED_SESSION_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["PREPARED_SESSION_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["PREPARED_SESSION_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
        }
        foreach ($Name in $Values.Keys) {
            [Environment]::SetEnvironmentVariable($Name, $Values[$Name], "Process")
        }
        return $Saved
    } catch {
        $EnvironmentError = $_
        try {
            Exit-IsolatedEnvironment $Saved
        } catch {
            Write-Warning "The process environment could not be fully restored after setup failed."
        }
        throw $EnvironmentError
    }
}

function Exit-IsolatedEnvironment([hashtable]$Saved) {
    $CurrentNames = @(
        "APP_ENV", "APP_SECRET_KEY", "DATABASE_URL", "REDIS_URL", "PYTHONPATH",
        "BACKEND_CORS_ORIGINS", "VINTED_BASE_URL", "VINTED_DATADOME_COLLECTOR_URL",
        "EGRESS_DIAGNOSTIC_URL", "VINTED_DIRECT_CATALOG_ENABLED",
        "VINTED_DATADOME_COLLECTOR_ENABLED", "VINTED_AUTH_ENABLED",
        "ACTION_REQUESTS_ENABLED", "SCHEDULER_ENABLED", "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PREPARED_SESSION_QA_API_URL", "PREPARED_SESSION_QA_PWA_URL",
        "PREPARED_SESSION_QA_BROWSER_CHANNEL", "VITE_DEV_API_PROXY_TARGET",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"
    )
    foreach ($Name in $CurrentNames) {
        [Environment]::SetEnvironmentVariable($Name, $null, "Process")
    }
    foreach ($Name in $Saved.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $Saved[$Name], "Process")
    }
}

function Invoke-PythonChecked([string]$Label, [string[]]$Arguments) {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

function Invoke-IsolatedTestCycle([int]$Cycle) {
    $Suffix = [Guid]::NewGuid().ToString("N")
    $DatabaseName = "vinted_monitor_qa_$Suffix"
    $RoleName = "vinted_monitor_qa_$Suffix"
    $Password = [Guid]::NewGuid().ToString("N") + [Guid]::NewGuid().ToString("N")
    $LeaseToken = [Guid]::NewGuid().ToString("N")
    $SafeNamePattern = '^vinted_monitor_qa_[0-9a-f]{32}$'
    if ($DatabaseName -notmatch $SafeNamePattern -or $RoleName -notmatch $SafeNamePattern) {
        throw "Generated QA resource names failed the safety check."
    }

    $PrimaryError = $null
    $CleanupErrors = @()
    $PostgresResourcesAttempted = $false
    $RedisLeaseAcquired = $false
    $SavedEnvironment = $null
    $LocationPushed = $false
    $ApiProcess = $null
    $ViteProcess = $null
    $QaLogFiles = @()
    try {
        Acquire-RedisLease $LeaseToken
        $RedisLeaseAcquired = $true

        $PostgresResourcesAttempted = $true
        $CreateSql = "CREATE ROLE $RoleName LOGIN PASSWORD '$Password';`nCREATE DATABASE $DatabaseName TEMPLATE template0 OWNER $RoleName;"
        Invoke-PostgresAdmin $CreateSql | Out-Null

        $DatabaseUrl = "postgresql+psycopg://${RoleName}:${Password}@127.0.0.1:5432/${DatabaseName}"
        $SavedEnvironment = Enter-IsolatedEnvironment $DatabaseUrl
        Push-Location $BackendDir
        $LocationPushed = $true

        Write-Host "Cycle $Cycle/${Repeat}: migrating an isolated PostgreSQL database"
        Invoke-PythonChecked -Label "Alembic migration" -Arguments @("-m", "alembic", "upgrade", "head")
        if ($Scenario -eq "prepared-session-read-model") {
            Assert-TcpPortAvailable 8001
            Assert-TcpPortAvailable 5176
            $QaStateDir = Join-Path $env:TEMP "scrapyvinterino-qa"
            New-Item -ItemType Directory -Path $QaStateDir -Force | Out-Null
            $ApiOutLog = Join-Path $QaStateDir "prepared-session-api-$Suffix.out.log"
            $ApiErrLog = Join-Path $QaStateDir "prepared-session-api-$Suffix.err.log"
            $ViteOutLog = Join-Path $QaStateDir "prepared-session-vite-$Suffix.out.log"
            $ViteErrLog = Join-Path $QaStateDir "prepared-session-vite-$Suffix.err.log"
            $QaLogFiles = @($ApiOutLog, $ApiErrLog, $ViteOutLog, $ViteErrLog)

            $ApiProcess = Start-Process `
                -FilePath $Python `
                -ArgumentList @("-m", "uvicorn", "vinted_monitor.api.main:app", "--host", "127.0.0.1", "--port", "8001") `
                -WorkingDirectory $BackendDir `
                -WindowStyle Hidden `
                -RedirectStandardOutput $ApiOutLog `
                -RedirectStandardError $ApiErrLog `
                -PassThru
            Wait-HttpReady "http://127.0.0.1:8001/health" 45 $ApiProcess

            $ViteProcess = Start-Process `
                -FilePath "cmd.exe" `
                -ArgumentList @("/d", "/s", "/c", "pnpm.cmd exec vite --host 127.0.0.1 --port 5176 --strictPort") `
                -WorkingDirectory $FrontendDir `
                -WindowStyle Hidden `
                -RedirectStandardOutput $ViteOutLog `
                -RedirectStandardError $ViteErrLog `
                -PassThru
            Wait-HttpReady "http://127.0.0.1:5176" 45 $ViteProcess
            Write-Host "Cycle $Cycle/${Repeat}: live API 8001 and strict Vite 5176 are isolated and ready"
        }
        Write-Host "Cycle $Cycle/${Repeat}: running audited scenario '$Scenario'"
        if ($Scenario -eq "full") {
            Invoke-PythonChecked `
                -Label "Backend suite" `
                -Arguments @("-m", "pytest", "-q", "tests", "--ignore=tests/test_catalog_failstop_integration.py")
            $env:VINTED_BASE_URL = "http://127.0.0.1:9"
            $env:VINTED_DATADOME_COLLECTOR_URL = "http://127.0.0.1:9"
            $env:EGRESS_DIAGNOSTIC_URL = "http://127.0.0.1:9"
            $env:VINTED_DIRECT_CATALOG_ENABLED = "false"
            $env:VINTED_DATADOME_COLLECTOR_ENABLED = "false"
            $env:VINTED_AUTH_ENABLED = "false"
            $env:ACTION_REQUESTS_ENABLED = "false"
            $env:SCHEDULER_ENABLED = "false"
            Invoke-PythonChecked `
                -Label "Loopback-guarded catalog integration" `
                -Arguments @("-m", "pytest", "-q", "tests/test_catalog_failstop_integration.py")
        } else {
            Invoke-PythonChecked -Label "Selected integration tests" -Arguments (@("-m", "pytest", "-q") + $ScenarioTargets)
        }
    } catch {
        $PrimaryError = $_
    } finally {
        foreach ($OwnedProcess in @($ViteProcess, $ApiProcess)) {
            if ($null -ne $OwnedProcess) {
                try {
                    Stop-OwnedProcessTree $OwnedProcess
                } catch {
                    $CleanupErrors += $_.Exception.Message
                }
            }
        }
        foreach ($QaLogFile in $QaLogFiles) {
            try {
                Remove-Item -LiteralPath $QaLogFile -ErrorAction SilentlyContinue
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
        }
        if ($LocationPushed) {
            try {
                Pop-Location
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
        }
        if ($null -ne $SavedEnvironment) {
            try {
                Exit-IsolatedEnvironment $SavedEnvironment
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
        }
        if ($PostgresResourcesAttempted) {
            try {
                if ($DatabaseName -notmatch $SafeNamePattern -or $RoleName -notmatch $SafeNamePattern) {
                    throw "QA resource names changed before cleanup."
                }
                $DropSql = "DROP DATABASE IF EXISTS $DatabaseName WITH (FORCE);`nDROP ROLE IF EXISTS $RoleName;"
                Invoke-PostgresAdmin $DropSql | Out-Null
                $VerifySql = "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_database WHERE datname = '$DatabaseName') OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$RoleName') THEN 'present' ELSE 'absent' END;"
                $State = @(Invoke-PostgresAdmin $VerifySql)
                if ($State.Count -eq 0 -or ([string]$State[-1]).Trim() -ne "absent") {
                    throw "PostgreSQL QA resources still exist after cleanup."
                }
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
        }
        if ($RedisLeaseAcquired) {
            try {
                Clear-OwnedRedisDatabase $LeaseToken
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
        }
    }

    if ($CleanupErrors.Count -gt 0) {
        $CleanupMessage = $CleanupErrors -join " "
        if ($null -ne $PrimaryError) {
            Write-Warning "The test failed and cleanup also reported: $CleanupMessage"
            throw $PrimaryError
        }
        throw $CleanupMessage
    }
    if ($null -ne $PrimaryError) {
        throw $PrimaryError
    }
    Write-Host "Cycle $Cycle/${Repeat}: isolated resources removed"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is required."
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Backend virtualenv Python was not found at $Python."
}
if (Test-Path -LiteralPath (Join-Path $BackendDir ".env")) {
    throw "backend/.env exists; refusing to run because Settings would load it."
}
foreach ($TestTarget in $ScenarioTargets) {
    $TestFile = ($TestTarget -split "::", 2)[0]
    $ResolvedTestFile = [IO.Path]::GetFullPath((Join-Path $BackendDir $TestFile))
    $IsTestsDirectory = $ResolvedTestFile.Equals($TestsDirectory, [StringComparison]::OrdinalIgnoreCase)
    $IsTestBelowRoot = $ResolvedTestFile.StartsWith($TestsRoot, [StringComparison]::OrdinalIgnoreCase)
    $PathType = if ($IsTestsDirectory) { "Container" } else { "Leaf" }
    if ((-not $IsTestsDirectory -and -not $IsTestBelowRoot) -or -not (Test-Path -LiteralPath $ResolvedTestFile -PathType $PathType)) {
        throw "Every audited target for scenario '$Scenario' must resolve to an existing file below backend/tests."
    }
}

$PostgresContainers = @(Get-RunningComposeContainers "postgres")
$RedisContainers = @(Get-RunningComposeContainers "redis")
if ($PostgresContainers.Count -ne 1 -or $RedisContainers.Count -ne 1) {
    throw "Expected exactly one running PostgreSQL and Redis container for project '$ComposeProject'."
}
$script:PostgresContainer = $PostgresContainers[0]
$script:RedisContainer = $RedisContainers[0]
Assert-ContainerBelongsToRepo $script:PostgresContainer "postgres"
Assert-ContainerBelongsToRepo $script:RedisContainer "redis"
Assert-ExecutorServicesStopped

$InitialPostgresDigest = Get-OperationalPostgresDigest
$InitialRedisDigest = Get-OperationalRedisDigest
for ($Cycle = 1; $Cycle -le $Repeat; $Cycle++) {
    Invoke-IsolatedTestCycle $Cycle
}
if ((Get-OperationalPostgresDigest) -ne $InitialPostgresDigest) {
    throw "Operational PostgreSQL changed during isolated QA; no automatic restoration was attempted."
}
if ((Get-OperationalRedisDigest) -ne $InitialRedisDigest) {
    throw "Operational Redis database 0 changed during isolated QA; no automatic restoration was attempted."
}
Assert-ExecutorServicesStopped
Write-Host "PASS: scenario '$Scenario', $Repeat isolated cycle(s); operational PostgreSQL/Redis fingerprints unchanged."
