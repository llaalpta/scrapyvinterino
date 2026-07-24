param(
    [ValidateSet("identity", "same-profile-recovery", "proxy-only-regression", "proxy-cooldown", "proxy-sticky-contract", "prepared-session-read-model", "monitor-identity-edit", "pwa-monitor-command-state", "pwa-bootstrap-isolation", "worker-redis-availability", "manual-session-start-baseline", "monitor-session-proxy-traffic", "recurring-session-start-baseline", "session-stop-drain", "full")]
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
$QaOwnerLabel = "com.scrapyvinterino.qa.owner"
$WorkerRedisFocusedTargets = @(
    "tests/test_scheduler_watchdog.py",
    "tests/test_scheduler_availability.py"
)
$WorkerRedisLiveTargets = @(
    "tests/test_worker_redis_availability_live.py::test_worker_redis_loss_exits_restarts_and_updates_live_pwa"
)
$SessionStopFocusedTargets = @(
    "tests/test_manual_runs.py::test_monitor_stop_commits_inactive_session_before_ready_task_cleanup",
    "tests/test_manual_runs.py::test_monitor_stop_drains_non_terminal_session_run",
    "tests/test_manual_runs.py::test_monitor_stop_still_rejects_sessionless_baseline_run",
    "tests/test_manual_runs.py::test_failed_run_preserves_failure_and_drain_waits_for_finalizing_sibling",
    "tests/test_search_sources.py::test_update_source_api_rejects_configuration_change_while_stop_is_draining"
)
$SessionStopLiveTargets = @(
    "tests/test_recurring_session_start_live.py::test_live_session_stop_drains_run_and_fences_reserved_task"
)
$MonitorIdentityFocusedTargets = @(
    "tests/test_search_sources.py::test_validate_search_source_name_enforces_database_length_after_trim",
    "tests/test_search_sources.py::test_search_source_create_schema_rejects_name_beyond_storage_limit",
    "tests/test_search_sources.py::test_update_source_api_persists_identity_on_same_monitor",
    "tests/test_search_sources.py::test_update_source_api_rejects_invalid_identity_without_mutation",
    "tests/test_search_sources.py::test_update_source_api_rejects_active_monitor_configuration_change",
    "tests/test_search_sources.py::test_update_source_api_rejects_configuration_change_while_stop_is_draining"
)
$MonitorIdentityLiveTargets = @(
    "tests/test_search_source_identity_live.py::test_live_monitor_identity_editing_contract"
)
$PwaMonitorCommandLiveTargets = @(
    "tests/test_pwa_monitor_command_state_live.py::test_live_pwa_monitor_command_state_contract"
)
$PwaBootstrapIsolationLiveTargets = @(
    "tests/test_pwa_bootstrap_isolation_live.py::test_live_pwa_bootstrap_failures_do_not_hide_monitors"
)
$ProxyStickyContractLiveTargets = @(
    "tests/test_proxy_sticky_contract_live.py::test_live_proxy_sticky_contract_edit_invalidates_and_rotates_context"
)
$SameProfileRecoveryLiveTargets = @(
    "tests/test_same_profile_recovery_live.py::test_live_pwa_same_profile_recovery_and_repeated_egress_rejection"
)
$MonitorSessionProxyTrafficFocusedTargets = @(
    "tests/test_monitor_proxy_traffic.py"
)
$MonitorSessionProxyTrafficActivationTargets = @(
    "tests/test_manual_runs.py::test_monitor_start_api_in_manual_mode_baselines_once_and_opens_session",
    "tests/test_manual_runs.py::test_monitor_start_api_baseline_failure_leaves_manual_monitor_inactive",
    "tests/test_manual_runs.py::test_recurring_monitor_start_baselines_then_opens_session_with_future_deadline",
    "tests/test_manual_runs.py::test_monitor_run_replaces_unusable_context_and_keeps_monitor_session_active",
    "tests/test_manual_runs.py::test_monitor_run_surfaces_expired_context_replacement_failure_without_hidden_retry"
)
$MonitorSessionProxyTrafficLiveTargets = @(
    "tests/test_manual_session_start_live.py::test_live_monitor_and_session_proxy_traffic_summary"
)
$TestTargets = @{
    "identity" = @(
        "tests/test_proxy_identity_fence.py::test_real_scheduler_producer_and_consumer_loop_preserve_stale_identity_fence"
    )
    "same-profile-recovery" = @(
        "tests/test_catalog_failstop_integration.py",
        "tests/test_migrations.py::test_honest_found_metrics_migration_removes_historical_event_field",
        "tests/test_manual_runs.py::test_detail_failure_retries_once_in_run_then_closes_candidate",
        "tests/test_manual_runs.py::test_datadome_mid_batch_rolls_back_and_discards_claimed_work",
        "tests/test_manual_runs.py::test_detail_session_context_failure_is_fail_stop_without_retry",
        "tests/test_manual_runs.py::test_gone_detail_is_terminal_without_retry",
        "tests/test_item_detail_state_audit.py::test_transient_release_failure_after_challenge_keeps_terminal_run_and_discards_work",
        "tests/test_item_detail_state_audit.py::test_release_failure_does_not_mask_primary_run_error"
    ) + $SameProfileRecoveryLiveTargets
    "proxy-only-regression" = @(
        "tests/test_manual_runs.py"
    )
    "proxy-cooldown" = @(
        "tests/test_proxies.py",
        "tests/test_migrations.py::test_proxy_test_telemetry_migration_drops_obsolete_columns",
        "tests/test_manual_runs.py::test_monitor_start_classifies_terminal_baseline_without_generic_proxy_penalty",
        "tests/test_proxy_identity_fence.py::test_manual_api_stale_proxy_selection_never_constructs_provider",
        "tests/test_proxy_identity_fence.py::test_redis_consumer_stale_proxy_selection_is_terminal_and_acknowledged",
        "tests/test_scheduler.py::test_scheduler_runner_enqueues_due_monitor_task",
        "tests/test_scheduler.py::test_scheduler_runner_respects_proxy_capacity_for_due_batch",
        "tests/test_scheduler.py::test_archive_cancels_scheduler_task_that_is_still_ready"
    )
    "proxy-sticky-contract" = @(
        "tests/test_proxies.py",
        "tests/test_migrations.py::test_proxy_sticky_contract_migration_backfills_non_null_profile_fields",
        "tests/test_manual_runs.py::test_prepared_context_save_and_refresh_use_earlier_global_or_profile_ttl",
        "tests/test_proxy_identity_fence.py::test_invalid_sticky_contract_does_not_mutate_identity_or_prepared_context"
    ) + $ProxyStickyContractLiveTargets
    "prepared-session-read-model" = @(
        "tests/test_prepared_session_read_model.py",
        "tests/test_prepared_session_live_contract.py::test_live_prepared_session_read_model_matches_runtime_and_pwa"
    )
    "worker-redis-availability" = @($WorkerRedisFocusedTargets + $WorkerRedisLiveTargets)
    "manual-session-start-baseline" = @(
        "tests/test_manual_session_start_live.py::test_live_manual_session_start_baseline_lifecycle"
    )
    "monitor-session-proxy-traffic" = @($MonitorSessionProxyTrafficLiveTargets)
    "recurring-session-start-baseline" = @(
        "tests/test_recurring_session_start_live.py::test_live_recurring_session_start_baseline_and_real_consumer",
        "tests/test_migrations.py::test_scheduler_ui_gate_migration_removes_persisted_enabled_field",
        "tests/test_migrations.py::test_proxy_only_catalog_migration_removes_persisted_direct_fields",
        "tests/test_scheduler.py::test_scheduler_state_uses_deployment_gate_and_runtime_dependencies",
        "tests/test_scheduler.py::test_scheduler_api_does_not_expose_removed_runtime_fields",
        "tests/test_scheduler.py::test_scheduler_api_rejects_removed_runtime_fields",
        "tests/test_scheduler.py::test_scheduler_config_rejects_unknown_persisted_runtime_fields",
        "tests/test_scheduler.py::test_scheduler_proxy_capacity_uses_proxy_profile_limits",
        "tests/test_manual_runs.py::test_monitor_run_api_returns_conflict_when_no_egress_capacity",
        "tests/test_task_queue_reliability_audit.py::test_proxyless_legacy_payload_dead_letters_exactly_once_and_releases_markers",
        "tests/test_scheduler_availability.py::test_scheduler_state_requires_fresh_producer_heartbeat",
        "tests/test_scheduler_availability.py::test_scheduler_runner_writes_heartbeat_while_deployment_gate_is_disabled",
        "tests/test_scheduler_availability.py::test_recurring_start_returns_503_without_producer_heartbeat",
        "tests/test_search_sources.py::test_scheduler_api_rejects_removed_ui_gate_without_mutating_settings"
    )
    "session-stop-drain" = @($SessionStopFocusedTargets + $SessionStopLiveTargets)
    "monitor-identity-edit" = @($MonitorIdentityFocusedTargets + $MonitorIdentityLiveTargets)
    "pwa-monitor-command-state" = @($PwaMonitorCommandLiveTargets)
    "pwa-bootstrap-isolation" = @($PwaBootstrapIsolationLiveTargets)
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
    if ($null -eq $Process) {
        return
    }
    if (-not $Process.HasExited) {
        & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0 -and -not $Process.HasExited) {
            throw "Could not stop owned QA process $($Process.Id)."
        }
    }
    if (-not $Process.WaitForExit(10000)) {
        throw "Owned QA process $($Process.Id) did not exit in time."
    }
    $Process.Dispose()
}

function Remove-OwnedQaFile([string]$Path, [string]$QaRoot) {
    $ResolvedRoot = [IO.Path]::GetFullPath($QaRoot)
    $ResolvedPath = [IO.Path]::GetFullPath($Path)
    $RootPrefix = $ResolvedRoot
    if (-not $RootPrefix.EndsWith([string][IO.Path]::DirectorySeparatorChar)) {
        $RootPrefix += [IO.Path]::DirectorySeparatorChar
    }
    if (-not $ResolvedPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove QA file outside the isolated state directory."
    }
    for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
        if (-not (Test-Path -LiteralPath $ResolvedPath)) {
            return
        }
        try {
            Remove-Item -LiteralPath $ResolvedPath -Force -ErrorAction Stop
        } catch {
            if ($Attempt -eq 20) {
                throw
            }
        }
        if (Test-Path -LiteralPath $ResolvedPath) {
            Start-Sleep -Milliseconds 100
        }
    }
    throw "QA temporary file still exists after bounded cleanup: $ResolvedPath"
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

function Invoke-DockerChecked([string]$Label, [string[]]$Arguments) {
    $Output = @(& docker @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed."
    }
    return $Output
}

function Test-DockerContainerExists([string]$Container) {
    $Output = @(& docker container ls --all --filter "name=^/${Container}$" --format "{{.Names}}" 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect disposable Docker containers."
    }
    return @($Output | Where-Object { ([string]$_).Trim() -eq $Container }).Count -eq 1
}

function Test-DockerNetworkExists([string]$Network) {
    $Output = @(& docker network ls --filter "name=^${Network}$" --format "{{.Name}}" 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect disposable Docker networks."
    }
    return @($Output | Where-Object { ([string]$_).Trim() -eq $Network }).Count -eq 1
}

function Assert-OwnedQaContainer([string]$Container, [string]$OwnerToken) {
    $Output = @(Invoke-DockerChecked "QA container ownership inspection" @(
        "inspect", "--format", "{{json .Config.Labels}}", $Container
    ))
    try {
        $Labels = ([string]$Output[-1]) | ConvertFrom-Json
        $Owner = $Labels.PSObject.Properties[$QaOwnerLabel].Value
    } catch {
        throw "QA container '$Container' has invalid ownership labels."
    }
    if ($Owner -ne $OwnerToken) {
        throw "Refusing to control container '$Container' without the expected QA ownership."
    }
}

function Assert-OwnedQaNetwork([string]$Network, [string]$OwnerToken) {
    $Output = @(Invoke-DockerChecked "QA network ownership inspection" @(
        "network", "inspect", "--format", "{{json .Labels}}", $Network
    ))
    try {
        $Labels = ([string]$Output[-1]) | ConvertFrom-Json
        $Owner = $Labels.PSObject.Properties[$QaOwnerLabel].Value
    } catch {
        throw "QA network '$Network' has invalid ownership labels."
    }
    if ($Owner -ne $OwnerToken) {
        throw "Refusing to control network '$Network' without the expected QA ownership."
    }
}

function Get-ContainerNetworkNames([string]$Container) {
    $Output = @(Invoke-DockerChecked "Container network inspection" @(
        "inspect", "--format", "{{json .NetworkSettings.Networks}}", $Container
    ))
    try {
        $Networks = ([string]$Output[-1]) | ConvertFrom-Json
        return @($Networks.PSObject.Properties.Name | Sort-Object -Unique)
    } catch {
        throw "Container '$Container' returned invalid network state."
    }
}

function Assert-ContainerNetworksExact([string]$Container, [string[]]$Expected) {
    $ExpectedNames = @($Expected | Sort-Object -Unique)
    $ActualNames = @(Get-ContainerNetworkNames $Container)
    $Difference = @(Compare-Object -ReferenceObject $ExpectedNames -DifferenceObject $ActualNames)
    if ($Difference.Count -gt 0) {
        throw "Container '$Container' network attachments do not match the initial snapshot."
    }
}

function Get-ContainerImageId([string]$Container) {
    $Output = @(Invoke-DockerChecked "Container image inspection" @(
        "inspect", "--format", "{{.Image}}", $Container
    ))
    $ImageId = ([string]$Output[-1]).Trim()
    if ($ImageId -notmatch '^sha256:[0-9a-f]{64}$') {
        throw "Container '$Container' returned an invalid local image id."
    }
    Invoke-DockerChecked "Local image verification" @("image", "inspect", $ImageId) | Out-Null
    return $ImageId
}

function Get-LocalBackendImageId {
    $ComposeFile = Join-Path $RepoRoot "docker-compose.yml"
    foreach ($Service in @("worker", "api")) {
        $Output = @(& docker compose --project-directory $RepoRoot -f $ComposeFile images --quiet $Service 2>&1)
        if ($LASTEXITCODE -ne 0) {
            throw "Could not inspect the local Compose image for '$Service'."
        }
        $Candidates = @($Output | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ } | Sort-Object -Unique)
        foreach ($Candidate in $Candidates) {
            $ImageOutput = @(& docker image inspect --format "{{.Id}}" $Candidate 2>&1)
            if ($LASTEXITCODE -eq 0 -and $ImageOutput.Count -eq 1) {
                $ImageId = ([string]$ImageOutput[0]).Trim()
                if ($ImageId -match '^sha256:[0-9a-f]{64}$') {
                    return $ImageId
                }
            }
        }
    }
    throw "No existing local worker/API image is available; refusing to pull or build during isolated QA."
}

function Wait-RedisContainerReady([string]$Container, [string]$OwnerToken, [int]$Seconds) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    do {
        Assert-OwnedQaContainer $Container $OwnerToken
        $Output = @(& docker exec $Container redis-cli --raw PING 2>&1)
        if ($LASTEXITCODE -eq 0 -and $Output.Count -gt 0 -and ([string]$Output[-1]).Trim() -eq "PONG") {
            return
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for the disposable Redis container."
}

function Remove-OwnedQaContainer(
    [string]$Container,
    [string]$OwnerToken,
    [bool]$DisableRestart
) {
    if (-not (Test-DockerContainerExists $Container)) {
        return
    }
    Assert-OwnedQaContainer $Container $OwnerToken
    if ($DisableRestart) {
        Invoke-DockerChecked "Disabling disposable worker restart" @(
            "update", "--restart=no", $Container
        ) | Out-Null
    }
    Assert-OwnedQaContainer $Container $OwnerToken
    Invoke-DockerChecked "Removing disposable QA container" @(
        "rm", "--force", $Container
    ) | Out-Null
    if (Test-DockerContainerExists $Container) {
        throw "Disposable QA container '$Container' still exists after cleanup."
    }
}

function Remove-OwnedQaNetwork(
    [string]$Network,
    [string]$OwnerToken,
    [string]$PostgresContainer,
    [string[]]$InitialPostgresNetworks
) {
    if (Test-DockerNetworkExists $Network) {
        Assert-OwnedQaNetwork $Network $OwnerToken
        if (@(Get-ContainerNetworkNames $PostgresContainer) -contains $Network) {
            Invoke-DockerChecked "Disconnecting operational PostgreSQL from QA network" @(
                "network", "disconnect", $Network, $PostgresContainer
            ) | Out-Null
        }
        Assert-OwnedQaNetwork $Network $OwnerToken
        Invoke-DockerChecked "Removing disposable QA network" @(
            "network", "rm", $Network
        ) | Out-Null
        if (Test-DockerNetworkExists $Network) {
            throw "Disposable QA network '$Network' still exists after cleanup."
        }
    }
    Assert-ContainerNetworksExact $PostgresContainer $InitialPostgresNetworks
}

function Invoke-PostgresAdmin([string]$Sql) {
    $Output = @($Sql | & docker exec -i $script:PostgresContainer sh -c 'psql -X -v ON_ERROR_STOP=1 -Atq -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "A PostgreSQL QA administration command failed."
    }
    return $Output
}

function Seed-ProxyOnlyMigrationLegacyState([string]$DatabaseName) {
    $Sql = @"
\connect $DatabaseName
INSERT INTO app_settings (key, value)
VALUES (
    'scheduler',
    '{"allow_direct_without_proxy":true,"direct_max_concurrent_runs":3,"max_concurrent_runs":2}'::jsonb
);
"@
    Invoke-PostgresAdmin $Sql | Out-Null
}

function Assert-ProxyOnlyMigrationDataTransform([string]$DatabaseName) {
    $Sql = @"
\connect $DatabaseName
SELECT (
    NOT (value ? 'allow_direct_without_proxy')
    AND NOT (value ? 'direct_max_concurrent_runs')
    AND value ->> 'max_concurrent_runs' = '2'
)::text
FROM app_settings
WHERE key = 'scheduler';
DELETE FROM app_settings WHERE key = 'scheduler';
"@
    $Output = @(Invoke-PostgresAdmin $Sql | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($Output.Count -ne 1 -or $Output[0] -ne "true") {
        throw "Migration 0022 did not remove only the obsolete direct scheduler fields."
    }
}

function Seed-ProxyCooldownMigrationLegacyState([string]$DatabaseName) {
    $Sql = @"
\connect $DatabaseName
INSERT INTO proxy_profiles (
    name, scheme, kind, host, port, country_code, locale, accept_language,
    screen, vinted_screen, max_concurrent_runs, is_active, failure_count,
    identity_generation, last_test_status, last_test_ip, last_test_error
) VALUES (
    'qa migration proxy cooldown', 'http', 'residential', 'proxy.invalid', 8080,
    'ES', 'es-ES', 'en-GB,en;q=0.9', '1920x1080', 'catalog', 1, false, 2,
    1, 'failed', '192.0.2.1', 'legacy diagnostic'
);
"@
    Invoke-PostgresAdmin $Sql | Out-Null
}

function Assert-ProxyCooldownMigrationDataTransform([string]$DatabaseName) {
    $Sql = @"
\connect $DatabaseName
SELECT (
    NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'proxy_profiles'
          AND column_name IN ('last_test_status', 'last_test_ip', 'last_test_error')
    )
    AND EXISTS (
        SELECT 1 FROM proxy_profiles
        WHERE name = 'qa migration proxy cooldown'
          AND failure_count = 2
          AND is_active = false
          AND sticky_username_template = '{username};sessid.{session_id}'
          AND sticky_ttl_minutes = 25
    )
)::text;
DELETE FROM proxy_profiles WHERE name = 'qa migration proxy cooldown';
"@
    $Output = @(Invoke-PostgresAdmin $Sql | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    if ($Output.Count -ne 1 -or $Output[0] -ne "true") {
        throw "Migrations 0023/0024 did not preserve and backfill the proxy profile contract."
    }
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
    $Pattern = '^(APP_|DATABASE_URL$|BACKEND_CORS_ORIGINS$|LOCAL_AUTH_|REDIS_URL$|SEEN_|VINTED_|WORKER_|CURL_|HUMAN_|DATADOME_|PROXY_|EGRESS_|SCHEDULER_|LOG_LEVEL$|ACTION_REQUESTS_|PYTHONPATH$|PYTEST_|ALEMBIC_|PREPARED_SESSION_QA_|MONITOR_IDENTITY_QA_|PWA_MONITOR_COMMAND_QA_|PWA_BOOTSTRAP_QA_|SAME_PROFILE_QA_|MANUAL_SESSION_QA_|RECURRING_SESSION_QA_|SESSION_STOP_QA_|SESSION_QA_|VITE_DEV_API_PROXY_TARGET$|HTTP_PROXY$|HTTPS_PROXY$|ALL_PROXY$|NO_PROXY$)'
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
        if ($Scenario -eq "monitor-identity-edit") {
            $Values["MONITOR_IDENTITY_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["MONITOR_IDENTITY_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["MONITOR_IDENTITY_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
        }
        if ($Scenario -eq "pwa-monitor-command-state") {
            $Values["PWA_MONITOR_COMMAND_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["PWA_MONITOR_COMMAND_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["PWA_MONITOR_COMMAND_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
        }
        if ($Scenario -eq "pwa-bootstrap-isolation") {
            $Values["PWA_BOOTSTRAP_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["PWA_BOOTSTRAP_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["PWA_BOOTSTRAP_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
        }
        if ($Scenario -eq "proxy-sticky-contract") {
            $Values["PROXY_STICKY_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["PROXY_STICKY_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["PROXY_STICKY_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
        }
        if ($Scenario -eq "same-profile-recovery") {
            $Values["SAME_PROFILE_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["SAME_PROFILE_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["SAME_PROFILE_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
            $Values["EGRESS_DIAGNOSTIC_URL"] = "http://127.0.0.2:9/qa/egress"
        }
        if ($Scenario -eq "worker-redis-availability") {
            $Values["REDIS_URL"] = "redis://127.0.0.1:9/0"
            $Values["SCHEDULER_ENABLED"] = "true"
            $Values["SCHEDULER_POLL_INTERVAL_SECONDS"] = "1"
            $Values["SCHEDULER_WORKER_HEARTBEAT_INTERVAL_SECONDS"] = "1"
            $Values["SCHEDULER_WORKER_HEARTBEAT_TIMEOUT_SECONDS"] = "5"
            $Values["SCHEDULER_WATCHDOG_POLL_INTERVAL_SECONDS"] = "1"
            $Values["SCHEDULER_WATCHDOG_STARTUP_GRACE_SECONDS"] = "5"
            $Values["WORKER_CONSUMER_COUNT"] = "1"
            $Values["WORKER_RESERVE_TIMEOUT_SECONDS"] = "1"
            $Values["WORKER_REDIS_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["WORKER_REDIS_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["WORKER_REDIS_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
        }
        if ($Scenario -in @("manual-session-start-baseline", "monitor-session-proxy-traffic")) {
            $Values["MANUAL_SESSION_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["MANUAL_SESSION_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["MANUAL_SESSION_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
        }
        if ($Scenario -eq "recurring-session-start-baseline") {
            $Values["VINTED_PREPARED_SESSION_REQUIRED"] = "false"
            $Values["SCHEDULER_ENABLED"] = "true"
            $Values["SCHEDULER_WORKER_HEARTBEAT_TIMEOUT_SECONDS"] = "600"
            $Values["WORKER_CONSUMER_COUNT"] = "1"
            $Values["WORKER_RESERVE_TIMEOUT_SECONDS"] = "1"
            $Values["RECURRING_SESSION_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["RECURRING_SESSION_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["RECURRING_SESSION_QA_BROWSER_CHANNEL"] = "chrome"
            $Values["VITE_DEV_API_PROXY_TARGET"] = "http://127.0.0.1:8001"
        }
        if ($Scenario -eq "session-stop-drain") {
            $Values["VINTED_PREPARED_SESSION_REQUIRED"] = "false"
            $Values["SCHEDULER_ENABLED"] = "true"
            $Values["SCHEDULER_WORKER_HEARTBEAT_TIMEOUT_SECONDS"] = "600"
            $Values["WORKER_CONSUMER_COUNT"] = "1"
            $Values["WORKER_RESERVE_TIMEOUT_SECONDS"] = "1"
            $Values["SESSION_STOP_QA_API_URL"] = "http://127.0.0.1:8001"
            $Values["SESSION_STOP_QA_PWA_URL"] = "http://127.0.0.1:5176"
            $Values["SESSION_STOP_QA_BROWSER_CHANNEL"] = "chrome"
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
        "EGRESS_DIAGNOSTIC_URL",
        "VINTED_DATADOME_COLLECTOR_ENABLED", "VINTED_AUTH_ENABLED",
        "ACTION_REQUESTS_ENABLED", "SCHEDULER_ENABLED", "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PREPARED_SESSION_QA_API_URL", "PREPARED_SESSION_QA_PWA_URL",
        "PREPARED_SESSION_QA_BROWSER_CHANNEL", "VITE_DEV_API_PROXY_TARGET",
        "MONITOR_IDENTITY_QA_API_URL", "MONITOR_IDENTITY_QA_PWA_URL",
        "MONITOR_IDENTITY_QA_BROWSER_CHANNEL",
        "PWA_BOOTSTRAP_QA_API_URL", "PWA_BOOTSTRAP_QA_PWA_URL",
        "PWA_BOOTSTRAP_QA_BROWSER_CHANNEL",
        "SAME_PROFILE_QA_API_URL", "SAME_PROFILE_QA_PWA_URL",
        "SAME_PROFILE_QA_BROWSER_CHANNEL", "SAME_PROFILE_QA_STATE",
        "MANUAL_SESSION_QA_API_URL", "MANUAL_SESSION_QA_PWA_URL",
        "MANUAL_SESSION_QA_BROWSER_CHANNEL", "MANUAL_SESSION_QA_PROVIDER_STATE",
        "RECURRING_SESSION_QA_API_URL", "RECURRING_SESSION_QA_PWA_URL",
        "RECURRING_SESSION_QA_BROWSER_CHANNEL", "SESSION_QA_PROVIDER_STATE",
        "SESSION_STOP_QA_API_URL", "SESSION_STOP_QA_PWA_URL",
        "SESSION_STOP_QA_BROWSER_CHANNEL",
        "SCHEDULER_POLL_INTERVAL_SECONDS", "SCHEDULER_WORKER_HEARTBEAT_INTERVAL_SECONDS",
        "SCHEDULER_WORKER_HEARTBEAT_TIMEOUT_SECONDS", "SCHEDULER_WATCHDOG_POLL_INTERVAL_SECONDS",
        "SCHEDULER_WATCHDOG_STARTUP_GRACE_SECONDS", "WORKER_CONSUMER_COUNT",
        "WORKER_RESERVE_TIMEOUT_SECONDS", "WORKER_TASK_QUEUE_KEY", "WORKER_REDIS_QA_API_URL",
        "WORKER_REDIS_QA_PWA_URL", "WORKER_REDIS_QA_BROWSER_CHANNEL",
        "WORKER_REDIS_QA_REDIS_CONTAINER", "WORKER_REDIS_QA_WORKER_CONTAINER",
        "WORKER_REDIS_QA_OWNER_TOKEN",
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
    $QaOwnerToken = [Guid]::NewGuid().ToString("N")
    $QaNetwork = "scrapyvinterino-qa-net-$Suffix"
    $QaRedisContainer = "scrapyvinterino-qa-redis-$Suffix"
    $QaWorkerContainer = "scrapyvinterino-qa-worker-$Suffix"
    $QaPostgresAlias = "qa-postgres-$Suffix"
    $SafeNamePattern = '^vinted_monitor_qa_[0-9a-f]{32}$'
    $SafeQaDockerNamePattern = '^scrapyvinterino-qa-(net|redis|worker)-[0-9a-f]{32}$'
    if (
        $DatabaseName -notmatch $SafeNamePattern -or
        $RoleName -notmatch $SafeNamePattern -or
        $QaNetwork -notmatch $SafeQaDockerNamePattern -or
        $QaRedisContainer -notmatch $SafeQaDockerNamePattern -or
        $QaWorkerContainer -notmatch $SafeQaDockerNamePattern
    ) {
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
    $QaTemporaryFiles = @()
    $QaStateDir = $null
    $QaNetworkCreated = $false
    $InitialPostgresNetworks = @()
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
        if ($Scenario -eq "recurring-session-start-baseline") {
            Invoke-PythonChecked -Label "Alembic migration to 0021" -Arguments @("-m", "alembic", "upgrade", "0021")
            Seed-ProxyOnlyMigrationLegacyState $DatabaseName
            Invoke-PythonChecked -Label "Alembic migration to head" -Arguments @("-m", "alembic", "upgrade", "head")
            Assert-ProxyOnlyMigrationDataTransform $DatabaseName
        } elseif ($Scenario -eq "manual-session-start-baseline") {
            Invoke-PythonChecked -Label "Alembic migration to 0022" -Arguments @("-m", "alembic", "upgrade", "0022")
            Seed-ProxyCooldownMigrationLegacyState $DatabaseName
            Invoke-PythonChecked -Label "Alembic migration to head" -Arguments @("-m", "alembic", "upgrade", "head")
            Assert-ProxyCooldownMigrationDataTransform $DatabaseName
        } else {
            Invoke-PythonChecked -Label "Alembic migration" -Arguments @("-m", "alembic", "upgrade", "head")
        }

        if ($Scenario -eq "monitor-session-proxy-traffic") {
            Write-Host "Cycle $Cycle/${Repeat}: verifying proxy-traffic aggregation before live PWA startup"
            Invoke-PythonChecked `
                -Label "Monitor/session proxy-traffic focused contract" `
                -Arguments (@("-m", "pytest", "-q") + $MonitorSessionProxyTrafficFocusedTargets)
            Invoke-PythonChecked `
                -Label "Monitor/session baseline linkage contract" `
                -Arguments (@("-m", "pytest", "-q") + $MonitorSessionProxyTrafficActivationTargets)
        }

        if ($Scenario -eq "worker-redis-availability") {
            Write-Host "Cycle $Cycle/${Repeat}: verifying worker supervisor behavior before live worker startup"
            Invoke-PythonChecked `
                -Label "Worker supervisor focused tests" `
                -Arguments (@("-m", "pytest", "-q") + $WorkerRedisFocusedTargets)

            $InitialPostgresNetworks = @(Get-ContainerNetworkNames $script:PostgresContainer)
            Invoke-DockerChecked "Creating disposable internal QA network" @(
                "network", "create", "--driver", "bridge", "--internal",
                "--label", "$QaOwnerLabel=$QaOwnerToken", $QaNetwork
            ) | Out-Null
            $QaNetworkCreated = $true
            Assert-OwnedQaNetwork $QaNetwork $QaOwnerToken

            Invoke-DockerChecked "Connecting PostgreSQL to disposable QA network" @(
                "network", "connect", "--alias", $QaPostgresAlias,
                $QaNetwork, $script:PostgresContainer
            ) | Out-Null
            Assert-ContainerNetworksExact `
                $script:PostgresContainer `
                @($InitialPostgresNetworks + $QaNetwork)

            Invoke-DockerChecked "Starting disposable Redis container" @(
                "run", "--detach", "--name", $QaRedisContainer,
                "--network", $QaNetwork, "--network-alias", $QaRedisContainer,
                "--label", "$QaOwnerLabel=$QaOwnerToken",
                $script:RedisImageId
            ) | Out-Null
            Assert-OwnedQaContainer $QaRedisContainer $QaOwnerToken
            Wait-RedisContainerReady $QaRedisContainer $QaOwnerToken 30

            $WorkerDatabaseUrl = "postgresql+psycopg://${RoleName}:${Password}@${QaPostgresAlias}:5432/${DatabaseName}"
            $WorkerRedisUrl = "redis://${QaRedisContainer}:6379/0"
            $WorkerEnvironment = @{
                APP_ENV = "test"
                APP_SECRET_KEY = $env:APP_SECRET_KEY
                DATABASE_URL = $WorkerDatabaseUrl
                REDIS_URL = $WorkerRedisUrl
                PYTHONPATH = "/app/src"
                BACKEND_CORS_ORIGINS = "http://127.0.0.1:5176"
                HTTP_PROXY = "http://127.0.0.1:9"
                HTTPS_PROXY = "http://127.0.0.1:9"
                ALL_PROXY = "http://127.0.0.1:9"
                NO_PROXY = "$QaPostgresAlias,$QaRedisContainer,127.0.0.1,localhost,::1"
                VINTED_BASE_URL = "http://127.0.0.1:9"
                VINTED_DATADOME_COLLECTOR_URL = "http://127.0.0.1:9"
                EGRESS_DIAGNOSTIC_URL = "http://127.0.0.1:9"
                VINTED_DATADOME_COLLECTOR_ENABLED = "false"
                VINTED_AUTH_ENABLED = "false"
                ACTION_REQUESTS_ENABLED = "false"
                SCHEDULER_ENABLED = "true"
                SCHEDULER_POLL_INTERVAL_SECONDS = "1"
                SCHEDULER_WORKER_HEARTBEAT_INTERVAL_SECONDS = "1"
                SCHEDULER_WORKER_HEARTBEAT_TIMEOUT_SECONDS = "5"
                SCHEDULER_WATCHDOG_POLL_INTERVAL_SECONDS = "1"
                SCHEDULER_WATCHDOG_STARTUP_GRACE_SECONDS = "5"
                WORKER_CONSUMER_COUNT = "1"
                WORKER_RESERVE_TIMEOUT_SECONDS = "1"
            }
            $WorkerArguments = @(
                "run", "--detach", "--name", $QaWorkerContainer,
                "--restart", "unless-stopped", "--network", $QaNetwork,
                "--label", "$QaOwnerLabel=$QaOwnerToken",
                "--mount", "type=bind,source=$([IO.Path]::GetFullPath((Join-Path $BackendDir 'src'))),target=/app/src,readonly"
            )
            foreach ($Name in @($WorkerEnvironment.Keys | Sort-Object)) {
                $WorkerArguments += @("--env", "$Name=$($WorkerEnvironment[$Name])")
            }
            $WorkerArguments += @($script:BackendImageId, "python", "-m", "vinted_monitor.worker.main")
            Invoke-DockerChecked "Starting disposable worker container" $WorkerArguments | Out-Null
            Assert-OwnedQaContainer $QaWorkerContainer $QaOwnerToken

            $env:WORKER_REDIS_QA_REDIS_CONTAINER = $QaRedisContainer
            $env:WORKER_REDIS_QA_WORKER_CONTAINER = $QaWorkerContainer
            $env:WORKER_REDIS_QA_OWNER_TOKEN = $QaOwnerToken
        }

        if ($Scenario -in @("prepared-session-read-model", "monitor-identity-edit", "pwa-monitor-command-state", "pwa-bootstrap-isolation", "proxy-sticky-contract", "same-profile-recovery", "worker-redis-availability", "manual-session-start-baseline", "monitor-session-proxy-traffic", "recurring-session-start-baseline", "session-stop-drain")) {
            Assert-TcpPortAvailable 8001
            Assert-TcpPortAvailable 5176
            if ($Scenario -eq "monitor-session-proxy-traffic") {
                Push-Location $FrontendDir
                try {
                    & pnpm.cmd build
                    if ($LASTEXITCODE -ne 0) {
                        throw "Frontend production build failed with exit code $LASTEXITCODE."
                    }
                } finally {
                    Pop-Location
                }
            }
            $QaStateDir = Join-Path $env:TEMP "scrapyvinterino-qa"
            New-Item -ItemType Directory -Path $QaStateDir -Force | Out-Null
            $ApiOutLog = Join-Path $QaStateDir "$Scenario-api-$Suffix.out.log"
            $ApiErrLog = Join-Path $QaStateDir "$Scenario-api-$Suffix.err.log"
            $ViteOutLog = Join-Path $QaStateDir "$Scenario-vite-$Suffix.out.log"
            $ViteErrLog = Join-Path $QaStateDir "$Scenario-vite-$Suffix.err.log"
            $QaLogFiles = @($ApiOutLog, $ApiErrLog, $ViteOutLog, $ViteErrLog)

            $ApiApplication = "vinted_monitor.api.main:app"
            $ApiArguments = @("-m", "uvicorn", $ApiApplication, "--host", "127.0.0.1", "--port", "8001")
            if ($Scenario -eq "proxy-sticky-contract") {
                $ApiApplication = "proxy_sticky_contract_qa_app:app"
                $ApiArguments = @(
                    "-m", "uvicorn", $ApiApplication,
                    "--app-dir", (Join-Path $BackendDir "tests"),
                    "--host", "127.0.0.1", "--port", "8001"
                )
            } elseif ($Scenario -eq "same-profile-recovery") {
                $SameProfileStateFile = Join-Path $QaStateDir "$Scenario-state-$Suffix.json"
                $QaTemporaryFiles = @($SameProfileStateFile)
                $env:SAME_PROFILE_QA_STATE = $SameProfileStateFile
                $ApiApplication = "same_profile_recovery_qa_app:app"
                $ApiArguments = @(
                    "-m", "uvicorn", $ApiApplication,
                    "--app-dir", (Join-Path $BackendDir "tests"),
                    "--host", "127.0.0.1", "--port", "8001"
                )
            } elseif ($Scenario -in @("manual-session-start-baseline", "monitor-session-proxy-traffic", "recurring-session-start-baseline", "session-stop-drain")) {
                $ProviderStateFile = Join-Path $QaStateDir "$Scenario-provider-$Suffix.json"
                $QaTemporaryFiles = @($ProviderStateFile)
                $env:SESSION_QA_PROVIDER_STATE = $ProviderStateFile
                if ($Scenario -in @("manual-session-start-baseline", "monitor-session-proxy-traffic")) {
                    $env:MANUAL_SESSION_QA_PROVIDER_STATE = $ProviderStateFile
                } elseif ($Scenario -eq "session-stop-drain") {
                    $env:WORKER_TASK_QUEUE_KEY = "qa:session-stop-drain:$Suffix"
                } else {
                    $env:WORKER_TASK_QUEUE_KEY = "qa:recurring-session:$Suffix"
                }
                $ApiApplication = "manual_session_qa_app:app"
                $ApiArguments = @(
                    "-m", "uvicorn", $ApiApplication,
                    "--app-dir", (Join-Path $BackendDir "tests"),
                    "--host", "127.0.0.1", "--port", "8001"
                )
            }

            $ApiProcess = Start-Process `
                -FilePath $Python `
                -ArgumentList $ApiArguments `
                -WorkingDirectory $BackendDir `
                -WindowStyle Hidden `
                -RedirectStandardOutput $ApiOutLog `
                -RedirectStandardError $ApiErrLog `
                -PassThru
            Wait-HttpReady "http://127.0.0.1:8001/health" 45 $ApiProcess

            $ViteCommand = if ($Scenario -eq "monitor-session-proxy-traffic") {
                "pnpm.cmd exec vite preview --host 127.0.0.1 --port 5176 --strictPort"
            } else {
                "pnpm.cmd exec vite --host 127.0.0.1 --port 5176 --strictPort"
            }
            $ViteProcess = Start-Process `
                -FilePath "cmd.exe" `
                -ArgumentList @("/d", "/s", "/c", $ViteCommand) `
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
            $env:VINTED_DATADOME_COLLECTOR_ENABLED = "false"
            $env:VINTED_AUTH_ENABLED = "false"
            $env:ACTION_REQUESTS_ENABLED = "false"
            $env:SCHEDULER_ENABLED = "false"
            Invoke-PythonChecked `
                -Label "Loopback-guarded catalog integration" `
                -Arguments @("-m", "pytest", "-q", "tests/test_catalog_failstop_integration.py")
        } elseif ($Scenario -eq "worker-redis-availability") {
            Invoke-PythonChecked `
                -Label "Live worker Redis availability contract" `
                -Arguments (@("-m", "pytest", "-q") + $WorkerRedisLiveTargets)
        } elseif ($Scenario -eq "session-stop-drain") {
            Invoke-PythonChecked `
                -Label "Session-stop focused contract" `
                -Arguments (@("-m", "pytest", "-q") + $SessionStopFocusedTargets)
            Invoke-PythonChecked `
                -Label "Session-stop live integration" `
                -Arguments (@("-m", "pytest", "-q") + $SessionStopLiveTargets)
        } elseif ($Scenario -eq "monitor-identity-edit") {
            Invoke-PythonChecked `
                -Label "Monitor-identity focused contract" `
                -Arguments (@("-m", "pytest", "-q") + $MonitorIdentityFocusedTargets)
            Invoke-PythonChecked `
                -Label "Monitor-identity live integration" `
                -Arguments (@("-m", "pytest", "-q") + $MonitorIdentityLiveTargets)
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
        if ($Scenario -eq "worker-redis-availability" -and $QaNetworkCreated) {
            try {
                Remove-OwnedQaContainer $QaWorkerContainer $QaOwnerToken $true
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
            try {
                Remove-OwnedQaContainer $QaRedisContainer $QaOwnerToken $false
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
            try {
                Remove-OwnedQaNetwork `
                    $QaNetwork `
                    $QaOwnerToken `
                    $script:PostgresContainer `
                    $InitialPostgresNetworks
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
        }
        foreach ($QaLogFile in $QaLogFiles) {
            try {
                Remove-OwnedQaFile $QaLogFile $QaStateDir
            } catch {
                $CleanupErrors += $_.Exception.Message
            }
        }
        foreach ($QaTemporaryFile in $QaTemporaryFiles) {
            try {
                Remove-OwnedQaFile $QaTemporaryFile $QaStateDir
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
if ($Scenario -eq "worker-redis-availability") {
    $script:RedisImageId = Get-ContainerImageId $script:RedisContainer
    $script:BackendImageId = Get-LocalBackendImageId
}

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
