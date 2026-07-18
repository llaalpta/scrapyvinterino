import { FileText, RefreshCw } from 'lucide-react';
import type { Run, RunEvent } from '../../api';
import { formatDate } from '../../utils/format';
import { type RunActivityController, useRunActivity } from './runActivity';

export function RunsView({
  getSourceName,
  runs,
  onLoadRunEvents
}: {
  getSourceName: (sourceId: number) => string;
  runs: Run[];
  onLoadRunEvents: (runId: number) => Promise<RunEvent[]>;
}) {
  const activity = useRunActivity(runs, onLoadRunEvents);

  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Monitor</h3>
        <span>{runs.length}</span>
      </div>
      <RunActivityList activity={activity} getSourceName={getSourceName} runs={runs} />
    </section>
  );
}

export function RunActivityList({
  activity,
  emptyText = 'Sin ejecuciones registradas.',
  getSourceName,
  runs,
  variant = 'cards'
}: {
  activity: RunActivityController;
  emptyText?: string;
  getSourceName: (sourceId: number) => string;
  runs: Run[];
  variant?: 'cards' | 'inline';
}) {
  if (runs.length === 0) {
    return <p className="empty-inline">{emptyText}</p>;
  }

  return (
    <div className={variant === 'inline' ? 'monitor-activity-list' : 'monitor-grid'}>
      {runs.map((run) => {
        const events = activity.eventsByRunId[run.id] ?? [];
        return (
          <article className={variant === 'inline' ? 'monitor-activity-row' : 'monitor-card'} key={run.id}>
            <div className="monitor-card-header">
              <div>
                <strong>{getSourceName(run.source_id)}</strong>
                <span>
                  Run #{run.id} - {triggerLabel(run.trigger)}
                </span>
              </div>
              <span className={`run-status ${run.status}`}>{run.status}</span>
            </div>
            <dl>
              <div>
                <dt>Inicio</dt>
                <dd>{formatDate(run.started_at)}</dd>
              </div>
              <div>
                <dt>Duracion</dt>
                <dd>{formatDuration(run.started_at, run.finished_at)}</dd>
              </div>
              <div>
                <dt>Encontrados</dt>
                <dd>{run.items_found}</dd>
              </div>
              <div>
                <dt>Nuevos monitor</dt>
                <dd>{run.items_new}</dd>
              </div>
              <div>
                <dt>Pasan</dt>
                <dd>{run.items_filter_passed}</dd>
              </div>
              <div>
                <dt>Descartados</dt>
                <dd>{run.items_discarded_by_filters}</dd>
              </div>
              <div>
                <dt>Sin detalle</dt>
                <dd>{run.items_filter_pending}</dd>
              </div>
              <div>
                <dt>Oportunidades</dt>
                <dd>{run.opportunities_created}</dd>
              </div>
            </dl>
            <div className="runtime-line">
              <span>Proxy: {proxyLabel(run.runtime_metadata)}</span>
              <span>Auth: {String(run.runtime_metadata.auth_mode ?? 'public_anonymous')}</span>
              <span>Filtros: {String(run.runtime_metadata.filter_count ?? 0)}</span>
            </div>
            {run.error_message ? <p className="run-error">{run.error_message}</p> : null}
            <button type="button" onClick={() => void activity.toggleLogs(run.id)}>
              <FileText size={16} />
              {activity.openRunId === run.id ? 'Cerrar logs' : 'Abrir logs'}
            </button>
            {activity.openRunId === run.id ? (
              <div className="run-events">
                {activity.loadingRunId === run.id ? <p>Cargando logs...</p> : null}
                {!activity.loadingRunId && events.length === 0 ? <p>Sin eventos para este run.</p> : null}
                <div className={`event-stream-status ${activity.streamStatus}`}>
                  <RefreshCw size={14} />
                  <span>{streamLabel(activity.streamStatus)}</span>
                </div>
                {events.map((event) => (
                  <RunEventEntry event={event} key={event.id} />
                ))}
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

export function RunEventEntry({ event, showRunId = false }: { event: RunEvent; showRunId?: boolean }) {
  const narrative = eventNarrative(event);
  const status = eventChecklistStatus(event, narrative);
  const logLines = eventRenderedLogLines(event, showRunId);
  return (
    <article className={`run-event-entry ${event.level}`}>
      <div className="event-console-line">
        <div className="event-check-meta">
          <span className="event-time">{formatLogTimestamp(event.created_at)}</span>
          <span className={`event-check-status ${status.tone}`}>{status.label}</span>
        </div>
        <div className="event-check-body">
          <div className="event-check-heading">
            <span className="event-check-title">{eventLabel(event.phase)}</span>
            {' '}
            <span className="event-check-phase">{narrative.area}.{narrative.action}</span>
            {narrative.result ? (
              <>
                {' '}
                <span className={`event-result ${narrative.tone}`}>{narrative.result}</span>
              </>
            ) : null}
          </div>
          {logLines.map((line, index) => (
            <div className={`event-log-line ${line.kind}`} key={`${line.kind}-${index}`}>{line.text}</div>
          ))}
        </div>
      </div>
    </article>
  );
}

type EventTone = 'neutral' | 'success' | 'warning' | 'error';

type EventNarrative = {
  area: string;
  action: string;
  result: string | null;
  tone: EventTone;
};

type ChecklistStatus = {
  label: 'INFO' | 'OK' | 'SKIP' | 'WARN' | 'FAIL';
  tone: EventTone;
};

type EventLogLine = {
  kind: 'operation' | 'context' | 'request' | 'checks' | 'outcome' | 'detail' | 'message';
  text: string;
};

const PHASE_NARRATIVES: Record<string, EventNarrative> = {
  run_started: { area: 'prepare_session', action: 'starting', result: null, tone: 'neutral' },
  run_config_resolved: { area: 'config', action: 'resolving', result: 'ok', tone: 'success' },
  run_succeeded: { area: 'run', action: 'finishing', result: 'ok', tone: 'success' },
  run_failed: { area: 'run', action: 'finishing', result: 'failed', tone: 'error' },
  baseline_required: { area: 'baseline', action: 'checking', result: 'required', tone: 'warning' },
  baseline_snapshot_seeded: { area: 'baseline', action: 'seeding', result: 'ok', tone: 'success' },
  egress_selected: { area: 'egress', action: 'selecting', result: 'ok', tone: 'success' },
  http_session_created: { area: 'http', action: 'opening', result: 'ok', tone: 'success' },
  http_session_closed: { area: 'http', action: 'closing', result: 'ok', tone: 'success' },
  egress_diagnostic_start: { area: 'egress', action: 'probing', result: null, tone: 'neutral' },
  egress_diagnostic_success: { area: 'egress', action: 'probing', result: 'ok', tone: 'success' },
  egress_diagnostic_reused: { area: 'egress', action: 'reusing', result: 'ok', tone: 'success' },
  egress_diagnostic_error: { area: 'egress', action: 'probing', result: 'failed', tone: 'error' },
  redis_check_start: { area: 'redis', action: 'checking', result: null, tone: 'neutral' },
  redis_check_success: { area: 'redis', action: 'checking', result: 'ok', tone: 'success' },
  redis_check_error: { area: 'redis', action: 'checking', result: 'failed', tone: 'error' },
  redis_seen_result: { area: 'redis', action: 'evaluating_seen', result: 'ok', tone: 'success' },
  redis_seen_marked: { area: 'redis', action: 'marking_seen', result: 'ok', tone: 'success' },
  redis_candidate_state_pending: { area: 'redis', action: 'staging_candidate_state', result: 'ok', tone: 'neutral' },
  redis_candidate_state_pending_error: { area: 'redis', action: 'staging_candidate_state', result: 'failed', tone: 'error' },
  redis_candidate_state_updated: { area: 'redis', action: 'committing_candidate_state', result: 'ok', tone: 'success' },
  redis_candidate_state_reconciled: { area: 'redis', action: 'reconciling_candidate_state', result: 'ok', tone: 'success' },
  candidate_seen_skipped: { area: 'candidate', action: 'deduplicating', result: 'skipped', tone: 'warning' },
  catalog_search_start: { area: 'catalog', action: 'searching', result: null, tone: 'neutral' },
  catalog_search_success: { area: 'catalog', action: 'searching', result: 'ok', tone: 'success' },
  catalog_candidates_received: { area: 'catalog', action: 'receiving_candidates', result: 'ok', tone: 'success' },
  anonymous_session_bootstrap_start: { area: 'bootstrap', action: 'requesting', result: null, tone: 'neutral' },
  anonymous_session_bootstrap_success: { area: 'bootstrap', action: 'extracting_context', result: 'ok', tone: 'success' },
  anonymous_session_bootstrap_error: { area: 'bootstrap', action: 'requesting', result: 'failed', tone: 'error' },
  anonymous_session_refresh_start: { area: 'bootstrap', action: 'refreshing', result: null, tone: 'warning' },
  anonymous_session_refresh_success: { area: 'bootstrap', action: 'refreshing', result: 'ok', tone: 'success' },
  catalog_session_context_ready: { area: 'session_context', action: 'checking', result: 'ok', tone: 'success' },
  catalog_session_context_incomplete: { area: 'session_context', action: 'checking', result: 'incomplete', tone: 'error' },
  vinted_session_prepare_start: { area: 'prepare_session', action: 'preparing', result: null, tone: 'neutral' },
  vinted_session_prepare_result: { area: 'session', action: 'saving', result: 'ok', tone: 'success' },
  detail_probe_finished: { area: 'detail_probe', action: 'finishing', result: 'ok', tone: 'success' },
  catalog_api_probe_start: { area: 'api', action: 'probing', result: null, tone: 'neutral' },
  catalog_api_probe_success: { area: 'api', action: 'probing', result: 'ok', tone: 'success' },
  catalog_api_probe_failed: { area: 'api', action: 'probing', result: 'rejected', tone: 'warning' },
  catalog_api_probe_error: { area: 'api', action: 'probing', result: 'failed', tone: 'error' },
  catalog_api_rate_limit_backoff: { area: 'api', action: 'waiting', result: 'rate_limited', tone: 'warning' },
  catalog_api_rate_limited: { area: 'api', action: 'requesting', result: 'rate_limited', tone: 'warning' },
  datadome_tags_request_start: { area: 'datadome', action: 'loading_tags', result: null, tone: 'neutral' },
  datadome_tags_request_success: { area: 'datadome', action: 'loading_tags', result: 'ok', tone: 'success' },
  datadome_tags_request_error: { area: 'datadome', action: 'loading_tags', result: 'failed', tone: 'warning' },
  datadome_collector_start: { area: 'datadome', action: 'checking', result: null, tone: 'neutral' },
  datadome_collector_attempt_start: { area: 'datadome', action: 'posting', result: null, tone: 'neutral' },
  datadome_collector_attempt_success: { area: 'datadome', action: 'posting', result: 'ok', tone: 'success' },
  datadome_collector_attempt_failed: { area: 'datadome', action: 'posting', result: 'no_cookie', tone: 'warning' },
  datadome_collector_success: { area: 'datadome', action: 'collecting', result: 'ok', tone: 'success' },
  datadome_collector_failed: { area: 'datadome', action: 'collecting', result: 'skipped', tone: 'warning' },
  datadome_collector_skipped: { area: 'datadome', action: 'checking', result: 'skipped', tone: 'warning' },
  datadome_challenge_detected: { area: 'datadome', action: 'detecting_challenge', result: 'challenge', tone: 'warning' },
  navigation_home_request_start: { area: 'navigation', action: 'visiting_home', result: null, tone: 'neutral' },
  navigation_home_request_success: { area: 'navigation', action: 'visiting_home', result: 'ok', tone: 'success' },
  navigation_home_request_error: { area: 'navigation', action: 'visiting_home', result: 'failed', tone: 'error' },
  navigation_delay_applied: { area: 'pacing', action: 'waiting', result: 'ok', tone: 'success' },
  human_delay_applied: { area: 'pacing', action: 'waiting', result: 'ok', tone: 'success' },
  catalog_api_request_start: { area: 'api', action: 'requesting', result: null, tone: 'neutral' },
  catalog_api_request_success: { area: 'api', action: 'requesting', result: 'ok', tone: 'success' },
  catalog_api_request_error: { area: 'api', action: 'requesting', result: 'failed', tone: 'error' },
  catalog_api_session_rejected: { area: 'api', action: 'requesting', result: 'rejected', tone: 'warning' },
  catalog_api_parse_error: { area: 'api', action: 'parsing', result: 'failed', tone: 'error' },
  candidate_evaluation_start: { area: 'candidate', action: 'evaluating', result: null, tone: 'neutral' },
  candidate_existing_opportunity_skipped: { area: 'candidate', action: 'checking_opportunity', result: 'skipped', tone: 'warning' },
  candidate_detail_required: { area: 'detail', action: 'checking_requirement', result: 'required', tone: 'neutral' },
  candidate_detail_not_required: { area: 'detail', action: 'checking_requirement', result: 'skipped', tone: 'success' },
  candidate_filter_decision: { area: 'filters', action: 'deciding', result: 'ok', tone: 'success' },
  detail_http_request_start: { area: 'detail', action: 'requesting', result: null, tone: 'neutral' },
  detail_http_request_success: { area: 'detail', action: 'requesting', result: 'ok', tone: 'success' },
  detail_http_request_error: { area: 'detail', action: 'requesting', result: 'failed', tone: 'error' },
  detail_parse_success: { area: 'detail', action: 'parsing', result: 'ok', tone: 'success' },
  detail_parse_error: { area: 'detail', action: 'parsing', result: 'failed', tone: 'error' },
  detail_batch_started: { area: 'detail', action: 'fetching_batch', result: null, tone: 'neutral' },
  detail_batch_finished: { area: 'detail', action: 'fetching_batch', result: 'ok', tone: 'success' },
  detail_batch_canary_failed: { area: 'detail', action: 'validating_batch', result: 'failed', tone: 'error' },
  detail_early_filter_shadow: { area: 'filters', action: 'observing_head', result: 'ok', tone: 'neutral' },
  detail_early_filter_enforced: { area: 'filters', action: 'rejecting_from_head', result: 'discarded', tone: 'warning' },
  detail_fetch_start: { area: 'detail', action: 'fetching', result: null, tone: 'neutral' },
  detail_fetch_joined: { area: 'detail', action: 'joining', result: 'ok', tone: 'success' },
  detail_fetch_success: { area: 'detail', action: 'fetching', result: 'ok', tone: 'success' },
  detail_fetch_error: { area: 'detail', action: 'fetching', result: 'failed', tone: 'error' },
  detail_fetch_skipped: { area: 'detail', action: 'fetching', result: 'skipped', tone: 'warning' },
  detail_fetch_early_discard: { area: 'filters', action: 'rejecting_from_head', result: 'discarded', tone: 'warning' },
  detail_candidates_claimed: { area: 'detail', action: 'claiming_candidates', result: 'ok', tone: 'success' },
  detail_candidate_batch_closed: { area: 'detail', action: 'closing_batch', result: 'discarded', tone: 'warning' },
  detail_candidate_lock_expiry_pending: { area: 'detail', action: 'waiting_lock_expiry', result: 'pending', tone: 'error' },
  // Historical-only phases remain readable in accumulated logs persisted before task 14.44.
  detail_candidate_recovery_staged: { area: 'detail', action: 'staging_recovery', result: 'ok', tone: 'success' },
  detail_retry_claimed: { area: 'detail', action: 'claiming_retry', result: 'ok', tone: 'success' },
  detail_retry_scheduled: { area: 'detail', action: 'scheduling_retry', result: 'ok', tone: 'warning' },
  detail_retry_batch_preserved: { area: 'detail', action: 'preserving_retry_batch', result: 'ok', tone: 'warning' },
  detail_retry_exhausted: { area: 'detail', action: 'exhausting_retry', result: 'failed', tone: 'error' },
  detail_incomplete: { area: 'detail', action: 'validating_required_fields', result: 'incomplete', tone: 'warning' },
  filter_passed: { area: 'filters', action: 'evaluating', result: 'passed', tone: 'success' },
  item_persisted: { area: 'item', action: 'persisting', result: 'ok', tone: 'success' },
  item_reused: { area: 'item', action: 'persisting', result: 'reused', tone: 'success' },
  item_detail_persisted: { area: 'item', action: 'persisting_detail', result: 'ok', tone: 'success' },
  item_discarded: { area: 'item', action: 'evaluating', result: 'discarded', tone: 'warning' },
  opportunity_created: { area: 'opportunity', action: 'creating', result: 'ok', tone: 'success' },
  opportunity_skipped_missing_detail: { area: 'opportunity', action: 'creating', result: 'missing_detail', tone: 'warning' },
  opportunity_skipped_incomplete_detail: { area: 'opportunity', action: 'creating', result: 'incomplete_detail', tone: 'warning' },
  opportunity_skipped: { area: 'opportunity', action: 'creating', result: 'already_exists', tone: 'warning' },
  candidate_persistence_finished: { area: 'persistence', action: 'finishing', result: 'ok', tone: 'success' },
  monitor_session_closed: { area: 'monitor_session', action: 'closing', result: 'ok', tone: 'success' }
};

function eventNarrative(event: RunEvent): EventNarrative {
  if (event.phase === 'run_started') {
    const trigger = detailString(event.details, 'trigger');
    return {
      area: trigger === 'session_prepare' ? 'prepare_session' : trigger === 'baseline' ? 'baseline' : trigger === 'detail_probe' ? 'detail_probe' : 'run',
      action: 'starting',
      result: null,
      tone: 'neutral'
    };
  }
  if (event.phase === 'run_succeeded' || event.phase === 'run_failed') {
    const sessionPrepare = event.details.session_prepare_run === true;
    const baseline = event.details.baseline_run === true;
    const detailProbe = event.details.detail_probe_run === true;
    return {
      area: sessionPrepare ? 'prepare_session' : baseline ? 'baseline' : detailProbe ? 'detail_probe' : 'run',
      action: 'finishing',
      result: event.phase === 'run_succeeded' ? 'ok' : 'failed',
      tone: event.phase === 'run_succeeded' ? 'success' : 'error'
    };
  }
  const configured = PHASE_NARRATIVES[event.phase];
  if (configured) {
    if (event.phase === 'vinted_session_prepare_result' && event.level === 'error') {
      return { ...configured, result: 'failed', tone: 'error' };
    }
    return configured;
  }
  const [area, ...rest] = event.phase.split('_');
  return {
    area: area || 'event',
    action: rest.length > 0 ? rest.join('_') : event.phase,
    result: event.level === 'error' ? 'failed' : event.level === 'warning' ? 'warning' : null,
    tone: event.level === 'error' ? 'error' : event.level === 'warning' ? 'warning' : 'neutral'
  };
}

function eventChecklistStatus(event: RunEvent, narrative: EventNarrative): ChecklistStatus {
  if (event.level === 'error') {
    return { label: 'FAIL', tone: 'error' };
  }
  if (narrative.result === 'skipped' || event.phase.endsWith('_skipped')) {
    return { label: 'SKIP', tone: 'warning' };
  }
  if (event.level === 'warning') {
    return { label: 'WARN', tone: 'warning' };
  }
  if (narrative.result === 'ok' || narrative.result === 'passed' || narrative.tone === 'success') {
    return { label: 'OK', tone: 'success' };
  }
  return { label: 'INFO', tone: 'neutral' };
}

function eventRenderedLogLines(event: RunEvent, showRunId: boolean): EventLogLine[] {
  const grouped = groupEventLineTokens(eventLineTokens(event, showRunId));
  const lines: EventLogLine[] = [];
  if (grouped.operation.length > 0) {
    lines.push({ kind: 'operation', text: grouped.operation.join(' ') });
  }
  if (grouped.context.length > 0) {
    lines.push({ kind: 'context', text: grouped.context.join(' ') });
  }
  if (grouped.request.length > 0) {
    lines.push({ kind: 'request', text: grouped.request.join(' ') });
  }
  if (grouped.checks.length > 0) {
    lines.push({ kind: 'checks', text: grouped.checks.join(' ') });
  }
  if (grouped.outcome.length > 0) {
    lines.push({ kind: 'outcome', text: grouped.outcome.join(' ') });
  }
  for (const detailLine of eventChecklistDetailLines(event)) {
    if (detailLine.startsWith('Configurado:') && grouped.request.length > 0) {
      continue;
    }
    lines.push({ kind: 'detail', text: detailLine });
  }
  const message = eventMessageToken(event);
  if (message) {
    lines.push({ kind: 'message', text: message });
  }
  return lines;
}

function groupEventLineTokens(tokens: string[]): Record<EventLogLine['kind'], string[]> {
  const grouped: Record<EventLogLine['kind'], string[]> = {
    operation: [],
    context: [],
    request: [],
    checks: [],
    outcome: [],
    detail: [],
    message: []
  };
  for (const token of tokens) {
    grouped[eventTokenGroup(token)].push(token);
  }
  return grouped;
}

function eventTokenGroup(token: string): EventLogLine['kind'] {
  if (isHttpMethodToken(token) || tokenStartsWith(token, ['run#', 'url=', 'status=', 'ms=', 'auth=', 'item=', 'items=', 'unique=', 'total=', 'marked=', 'seen=', 'new=', 'endpoint='])) {
    return 'operation';
  }
  if (tokenStartsWith(token, ['session=', 'proxy_session=', 'ip=', 'country=', 'imp=', 'browser=', 'locale=', 'viewport=', 'x_screen='])) {
    return 'context';
  }
  if (tokenStartsWith(token, ['headers=', 'default_headers=', 'cookies=', 'x-anon-id=', 'x-csrf-token=', 'x-v-udt=', 'post_sent=', 'non_blocking=', 'js=', 'ddv=', 'ddk=', 'dd_cookie='])) {
    return 'request';
  }
  if (tokenStartsWith(token, ['csrf=', 'anon=', 'access=', 'datadome=', 'v_udt=', 'cf_bm=', 'v_sid=', 'geo=', 'locale_ok=', 'viewport_ok=', 'x_screen_ok=', 'response_screen_ok='])) {
    return 'checks';
  }
  return 'outcome';
}

function tokenStartsWith(token: string, prefixes: string[]): boolean {
  return prefixes.some((prefix) => token.startsWith(prefix));
}

function isHttpMethodToken(token: string): boolean {
  return ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].includes(token);
}

function eventLineTokens(event: RunEvent, showRunId: boolean): string[] {
  const parts: string[] = [];
  if (showRunId && event.run_id) {
    parts.push(`run#${event.run_id}`);
  }
  if (event.method) {
    parts.push(event.method);
  }
  if (event.url) {
    parts.push(`url=${formatLogUrl(event.url)}`);
  }
  if (event.status_code) {
    parts.push(`status=${event.status_code}`);
  }
  if (event.duration_ms !== null) {
    parts.push(`ms=${event.duration_ms}`);
  }
  if (event.auth_mode) {
    parts.push(`auth=${event.auth_mode}`);
  }
  const headerSet = headerSetToken(event);
  if (headerSet) {
    parts.push(`headers=${headerSet}`);
  }
  if (event.details.default_headers === false) {
    parts.push('default_headers=off');
  }
  const itemId = detailString(event.details, 'vinted_item_id') || detailString(event.details, 'item_id');
  if (itemId) {
    parts.push(`item=${itemId}`);
  }
  const evaluationContract = detailString(event.details, 'evaluation_contract');
  if (evaluationContract) {
    parts.push(`contract=${evaluationContract}`);
  }
  const policyHash = detailString(event.details, 'policy_hash');
  if (policyHash) {
    parts.push(`policy=${policyHash}`);
  }
  const filterScope = detailString(event.details, 'filter_scope');
  if (filterScope) {
    parts.push(`filter_scope=${filterScope}`);
  }
  const earlyFilterMode = nestedDetailString(event.details, 'runtime_config', 'detail_early_filter_mode');
  if (earlyFilterMode) {
    parts.push(`early=${earlyFilterMode}`);
  }
  const bodyBytes = detailString(event.details, 'body_bytes_received');
  if (bodyBytes) {
    parts.push(`body_bytes=${bodyBytes}`);
  }
  const headBytes = detailString(event.details, 'head_bytes_observed');
  if (headBytes) {
    parts.push(`head_bytes=${headBytes}`);
  }
  const headLimit = detailString(event.details, 'head_max_bytes') || nestedDetailString(event.details, 'runtime_config', 'detail_head_max_bytes');
  if (headLimit) {
    parts.push(`head_limit=${headLimit}`);
  }
  const matchCount = detailString(event.details, 'match_count');
  if (matchCount) {
    parts.push(`matches=${matchCount}`);
  }
  const filterDuration = detailString(event.details, 'filter_duration_ms');
  if (filterDuration) {
    parts.push(`filter_ms=${filterDuration}`);
  }
  const evaluationStatus = detailString(event.details, 'evaluation_status');
  if (evaluationStatus) {
    parts.push(`evaluation=${evaluationStatus}`);
  }
  if (typeof event.details.opportunity_created === 'boolean') {
    parts.push(`opportunity=${event.details.opportunity_created ? 'created' : 'existing'}`);
  }
  const session = nestedDetailString(event.details, 'http_session', 'masked');
  if (session) {
    parts.push(`session=${session}`);
  }
  const proxySession = nestedDetailString(event.details, 'proxy_session', 'masked') || nestedDetailString(event.details, 'proxy_sticky_session', 'masked');
  if (proxySession) {
    parts.push(`proxy_session=${proxySession}`);
  }
  const ip = nestedDetailString(event.details, 'egress', 'ip') || (event.egress_ip ?? null);
  if (ip) {
    parts.push(`ip=${ip}`);
  }
  const country = nestedDetailString(event.details, 'egress', 'country_code') || nestedDetailString(event.details, 'egress', 'country');
  if (country) {
    parts.push(`country=${country}`);
  }
  const egressCountry = detailString(event.details, 'egress_country_code');
  if (egressCountry && !country) {
    parts.push(`country=${egressCountry}`);
  }
  const impersonate = detailString(event.details, 'impersonate');
  if (impersonate) {
    parts.push(`imp=${impersonate}`);
  }
  const browserProfile = detailString(event.details, 'browser_profile');
  if (browserProfile) {
    parts.push(`browser=${browserProfile}`);
  }
  appendCookieCount(parts, event.details);
  appendBooleanToken(parts, 'csrf', event.details, 'csrf_token_found');
  appendBooleanToken(parts, 'anon', event.details, 'anon_id_found');
  appendBooleanToken(parts, 'access', event.details, 'access_token_found');
  appendBooleanToken(parts, 'datadome', event.details, 'datadome_cookie');
  appendBooleanToken(parts, 'v_udt', event.details, 'v_udt_found');
  appendBooleanToken(parts, 'cf_bm', event.details, 'cf_bm_cookie');
  appendBooleanToken(parts, 'v_sid', event.details, 'v_sid_cookie');
  appendBooleanToken(parts, 'geo', event.details, 'egress_country_match');
  appendBooleanToken(parts, 'locale_ok', event.details, 'locale_configured');
  appendBooleanToken(parts, 'viewport_ok', event.details, 'viewport_configured');
  appendBooleanToken(parts, 'x_screen_ok', event.details, 'vinted_screen_configured');
  appendBooleanToken(parts, 'response_screen_ok', event.details, 'response_screen_matches');
  appendBooleanToken(parts, 'ddk', event.details, 'ddk_found');
  appendBooleanToken(parts, 'dd_cookie', event.details, 'cookie_found');
  if (typeof event.details.post_sent === 'boolean') {
    parts.push(`post_sent=${event.details.post_sent ? 'true' : 'false'}`);
  }
  if (typeof event.details.non_blocking === 'boolean') {
    parts.push(`non_blocking=${event.details.non_blocking ? 'true' : 'false'}`);
  }
  const collectorEndpoint = detailString(event.details, 'collector_endpoint');
  if (collectorEndpoint && !event.url) {
    parts.push(`endpoint=${formatLogUrl(collectorEndpoint)}`);
  }
  appendDynamicHeaderTokens(parts, event.details);
  const locale = detailString(event.details, 'locale');
  if (locale) {
    parts.push(`locale=${locale}`);
  }
  const viewport = detailString(event.details, 'viewport_size') || detailString(event.details, 'screen');
  if (viewport) {
    parts.push(`viewport=${viewport}`);
  }
  const vintedScreen = detailString(event.details, 'vinted_screen') || detailString(event.details, 'response_screen');
  if (vintedScreen) {
    parts.push(`x_screen=${vintedScreen}`);
  }
  const jsType = detailString(event.details, 'js_type');
  if (jsType) {
    parts.push(`js=${jsType}`);
  }
  const ddv = detailString(event.details, 'ddv');
  if (ddv) {
    parts.push(`ddv=${ddv}`);
  }
  const probeOutcome = detailString(event.details, 'probe_outcome') || detailString(event.details, 'outcome');
  if (probeOutcome) {
    parts.push(`probe=${probeOutcome}`);
  }
  const probeStatus = detailString(event.details, 'probe_status_code');
  if (probeStatus) {
    parts.push(`probe_status=${probeStatus}`);
  }
  const probeDuration = detailString(event.details, 'probe_duration_ms');
  if (probeDuration) {
    parts.push(`probe_ms=${probeDuration}`);
  }
  const contentType = detailString(event.details, 'content_type') || nestedDetailString(event.details, 'response', 'content_type');
  if (contentType) {
    parts.push(`content=${formatContentType(contentType)}`);
  }
  const error = detailString(event.details, 'error');
  if (error) {
    parts.push(`reason=${formatTokenValue(error)}`);
  }
  if (event.phase === 'catalog_candidates_received') {
    appendNumberToken(parts, 'items', event.details, 'candidate_count');
    appendNumberToken(parts, 'unique', event.details, 'unique_candidate_count');
    appendNumberToken(parts, 'total', event.details, 'total_entries');
  }
  if (event.phase === 'detail_candidates_claimed') {
    appendNumberToken(parts, 'candidates', event.details, 'candidate_count');
  }
  if (event.phase === 'detail_candidate_batch_closed' || event.phase === 'detail_candidate_lock_expiry_pending') {
    appendNumberToken(parts, 'discarded', event.details, 'discarded_candidate_count');
  }
  const directItems = detailString(event.details, 'items_count') || detailString(event.details, 'item_count');
  if (directItems) {
    parts.push(`items=${directItems}`);
  }
  const responseItems = directItems ? null : nestedDetailString(event.details, 'response', 'items_count');
  if (responseItems) {
    parts.push(`items=${responseItems}`);
  }
  if (event.phase === 'baseline_snapshot_seeded' || event.phase === 'redis_seen_marked') {
    appendNumberToken(parts, 'marked', event.details, 'marked_seen_count');
  }
  if (event.phase === 'redis_candidate_state_updated' || event.phase === 'redis_candidate_state_reconciled') {
    appendNumberToken(parts, 'marked', event.details, 'marked_seen_count');
    appendNumberToken(parts, 'retries', event.details, 'retry_scheduled_count');
  }
  if (event.phase === 'redis_seen_result') {
    appendNumberToken(parts, 'seen', event.details, 'seen_hit_count');
    appendNumberToken(parts, 'new', event.details, 'seen_miss_count');
  }
  const missingRequired = detailArray(event.details, 'missing_required');
  if (missingRequired.length > 0) {
    parts.push(`missing=${missingRequired.join('|')}`);
  }
  const retryAfter = detailString(event.details, 'retry_after_seconds');
  if (retryAfter) {
    parts.push(`retry_after=${retryAfter}s`);
  }
  const backoff = detailString(event.details, 'backoff_seconds');
  if (backoff) {
    parts.push(`backoff=${backoff}s`);
  }
  const savedSessionId = detailString(event.details, 'vinted_session_id');
  if (savedSessionId) {
    parts.push(`session_id=${savedSessionId}`);
  }
  const status = detailString(event.details, 'status') || detailString(event.details, 'vinted_session_status');
  if (status) {
    parts.push(`session_status=${status}`);
  }
  const useCount = detailString(event.details, 'vinted_session_use_count');
  const maxCount = detailString(event.details, 'vinted_session_max_requests');
  if (useCount && maxCount) {
    parts.push(`use_count=${useCount}/${maxCount}`);
  }
  return parts;
}

function eventChecklistDetailLines(event: RunEvent): string[] {
  const lines: string[] = [];
  const recovered = detailStringArray(event.details, 'recovered_context');
  if (recovered.length > 0) {
    lines.push(`Recuperado: ${recovered.join(', ')}`);
  }
  const missing = detailStringArray(event.details, 'missing_context');
  const missingRequired = detailStringArray(event.details, 'missing_required');
  const pending = missing.length > 0 ? missing : missingRequired;
  if (pending.length > 0) {
    lines.push(`Pendiente: ${pending.join(', ')}`);
  }
  const cookieFlags = detailStringArray(event.details, 'cookie_flags');
  if (cookieFlags.length > 0) {
    lines.push(`Cookies: ${cookieFlags.join(', ')}`);
  }
  const requestProfile = detailString(event.details, 'request_profile') || headerSetToken(event);
  const defaultHeaders = event.details.default_headers === false ? 'default_headers=off' : null;
  const dynamicHeaders = dynamicHeaderTokens(event.details);
  if (requestProfile || defaultHeaders || dynamicHeaders.length > 0) {
    lines.push(['Configurado:', requestProfile ? `headers=${requestProfile}` : null, defaultHeaders, ...dynamicHeaders].filter(Boolean).join(' '));
  }
  const params = detailRecord(event.details, 'api_param_summary') || detailRecord(event.details, 'api_params');
  const paramsLine = formatApiParams(params);
  if (paramsLine) {
    lines.push(`Parametros API: ${paramsLine}`);
  }
  const responseSummary = detailRecord(event.details, 'response_summary');
  const responseLine = formatResponseSummary(responseSummary);
  if (responseLine) {
    lines.push(`Respuesta: ${responseLine}`);
  }
  const detailSummary = formatDetailSummary(detailRecord(event.details, 'detail_summary'));
  if (detailSummary) {
    lines.push(`Detalle: ${detailSummary}`);
  }
  return lines;
}

function eventMessageToken(event: RunEvent): string | null {
  if (!event.message || event.level === 'info') {
    return null;
  }
  const error = detailString(event.details, 'error');
  if (error) {
    return null;
  }
  return `reason=${formatTokenValue(event.message)}`;
}

function headerSetToken(event: RunEvent): string | null {
  const requestProfile = detailString(event.details, 'request_profile');
  if (requestProfile) {
    return requestProfile;
  }
  if (!detailRecord(event.details, 'request_headers')) {
    return null;
  }
  if (event.phase.startsWith('anonymous_session_bootstrap')) {
    return 'bootstrap_har146';
  }
  if (event.phase.startsWith('catalog_api')) {
    return 'api_har146';
  }
  if (event.phase.startsWith('datadome_tags')) {
    return 'datadome_tags';
  }
  if (event.phase.startsWith('datadome_collector')) {
    return 'datadome_collector';
  }
  if (event.phase.startsWith('detail_http')) {
    return 'detail_har146';
  }
  return 'custom';
}

function appendCookieCount(parts: string[], details: Record<string, unknown>): void {
  const explicitCount = detailString(details, 'cookie_count') || detailString(details, 'session_marker_count');
  if (explicitCount) {
    parts.push(`cookies=${explicitCount}`);
    return;
  }
  const cookiesAfter = detailArray(details, 'cookies_after');
  if (cookiesAfter.length > 0) {
    parts.push(`cookies=${cookiesAfter.length}`);
    return;
  }
  const cookiesBefore = detailArray(details, 'cookies_before');
  if (cookiesBefore.length > 0) {
    parts.push(`cookies=${cookiesBefore.length}`);
  }
}

function appendDynamicHeaderTokens(parts: string[], details: Record<string, unknown>): void {
  parts.push(...dynamicHeaderTokens(details));
}

function dynamicHeaderTokens(details: Record<string, unknown>): string[] {
  const parts: string[] = [];
  const headers = detailRecord(details, 'request_headers');
  if (!headers) {
    return parts;
  }
  if (Object.prototype.hasOwnProperty.call(headers, 'x-anon-id')) {
    parts.push('x-anon-id=ok');
  }
  if (Object.prototype.hasOwnProperty.call(headers, 'x-csrf-token')) {
    parts.push('x-csrf-token=ok');
  }
  if (Object.prototype.hasOwnProperty.call(headers, 'x-v-udt')) {
    parts.push('x-v-udt=sent');
  } else if (Object.prototype.hasOwnProperty.call(headers, 'x-anon-id') || Object.prototype.hasOwnProperty.call(headers, 'x-csrf-token')) {
    parts.push('x-v-udt=not_sent');
  }
  return parts;
}

function appendBooleanToken(parts: string[], label: string, details: Record<string, unknown>, key: string): void {
  const value = details[key];
  if (typeof value === 'boolean') {
    parts.push(`${label}=${value ? 'ok' : 'missing'}`);
  }
}

function appendNumberToken(parts: string[], label: string, details: Record<string, unknown>, key: string): void {
  const value = details[key];
  if (typeof value === 'number') {
    parts.push(`${label}=${value}`);
  }
}

function detailString(details: Record<string, unknown>, key: string): string | null {
  const value = details[key];
  if (typeof value === 'string' && value) {
    return value;
  }
  if (typeof value === 'number') {
    return String(value);
  }
  return null;
}

function detailArray(details: Record<string, unknown>, key: string): unknown[] {
  const value = details[key];
  return Array.isArray(value) ? value : [];
}

function detailStringArray(details: Record<string, unknown>, key: string): string[] {
  return detailArray(details, key)
    .map((value) => {
      if (typeof value === 'string' && value) {
        return formatTokenValue(value);
      }
      if (typeof value === 'number' || typeof value === 'boolean') {
        return String(value);
      }
      return null;
    })
    .filter((value): value is string => Boolean(value));
}

function detailRecord(details: Record<string, unknown>, key: string): Record<string, unknown> | null {
  const value = details[key];
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function nestedDetailString(details: Record<string, unknown>, parent: string, key: string): string | null {
  const value = details[parent];
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return detailString(value as Record<string, unknown>, key);
}

function formatLogUrl(value: string): string {
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.replace(/^www\./, '');
    const suffix = parsed.search ? '?...' : '';
    return `${host}${parsed.pathname}${suffix}`;
  } catch {
    return formatTokenValue(value);
  }
}

function formatContentType(value: string): string {
  return value.split(';')[0].trim() || value;
}

function formatApiParams(params: Record<string, unknown> | null): string | null {
  if (!params) {
    return null;
  }
  const preferred = ['catalog_ids', 'brand_ids', 'status_ids', 'size_ids', 'price_to', 'currency', 'page', 'per_page', 'order'];
  const tokens = preferred
    .filter((key) => Object.prototype.hasOwnProperty.call(params, key))
    .map((key) => {
      const value = params[key];
      if (Array.isArray(value)) {
        return `${key}=${value.map(String).join('|')}`;
      }
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        return `${key}=${formatTokenValue(String(value))}`;
      }
      return null;
    })
    .filter((value): value is string => Boolean(value));
  return tokens.length > 0 ? tokens.join(' ') : null;
}

function formatResponseSummary(summary: Record<string, unknown> | null): string | null {
  if (!summary) {
    return null;
  }
  const tokens = ['cf_ray', 'cf_cache_status', 'request_id', 'upstream_ms']
    .map((key) => {
      const value = summary[key];
      if (typeof value === 'string' || typeof value === 'number') {
        return `${key}=${formatTokenValue(String(value))}`;
      }
      return null;
    })
    .filter((value): value is string => Boolean(value));
  return tokens.length > 0 ? tokens.join(' ') : null;
}

function formatDetailSummary(summary: Record<string, unknown> | null): string | null {
  if (!summary) {
    return null;
  }
  const tokens = [
    typeof summary.parser_version === 'string' ? `parser=${formatTokenValue(summary.parser_version)}` : null,
    summary.title_present === true ? 'title=ok' : summary.title_present === false ? 'title=missing' : null,
    typeof summary.title === 'string' && summary.title ? `title=${formatTokenValue(summary.title)}` : null,
    typeof summary.photo_count === 'number' ? `photos=${summary.photo_count}` : null,
    typeof summary.description_length === 'number' ? `description_chars=${summary.description_length}` : null,
    summary.seller_present === true ? 'seller=ok' : summary.seller_present === false ? 'seller=missing' : null,
    typeof summary.brand === 'string' && summary.brand ? `brand=${formatTokenValue(summary.brand)}` : null,
    typeof summary.size === 'string' && summary.size ? `size=${formatTokenValue(summary.size)}` : null,
    typeof summary.price_amount === 'string' && summary.price_amount ? `price=${formatTokenValue(summary.price_amount)}` : null,
    typeof summary.currency === 'string' && summary.currency ? `currency=${formatTokenValue(summary.currency)}` : null,
    typeof summary.availability_state === 'string'
      ? `availability=${formatTokenValue(summary.availability_state)}`
      : null
  ].filter(Boolean);
  return tokens.length > 0 ? tokens.join(' ') : null;
}

function formatTokenValue(value: string): string {
  const compact = value.replace(/\s+/g, '_');
  return compact.length > 96 ? `${compact.slice(0, 93)}...` : compact;
}

function formatLogTimestamp(value: string): string {
  const date = new Date(value);
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  const millis = String(date.getMilliseconds()).padStart(3, '0');
  return `${hours}:${minutes}:${seconds}.${millis}`;
}

function streamLabel(status: 'historical'): string {
  return status === 'historical' ? 'Historico cargado bajo demanda' : '';
}

function eventLabel(phase: string): string {
  const labels: Record<string, string> = {
    run_started: 'Run iniciado',
    run_config_resolved: 'Configuracion resuelta',
    run_succeeded: 'Run completado',
    run_failed: 'Run fallido',
    baseline_required: 'Snapshot inicial requerido',
    baseline_snapshot_seeded: 'Foto inicial guardada',
    egress_selected: 'Egress seleccionado',
    http_session_created: 'Sesion HTTP creada',
    http_session_closed: 'Sesion HTTP cerrada',
    egress_diagnostic_start: 'Diagnosticando IP de salida',
    egress_diagnostic_success: 'IP de salida diagnosticada',
    egress_diagnostic_reused: 'IP de salida reciente reutilizada',
    egress_diagnostic_error: 'Error diagnosticando IP',
    redis_check_start: 'Comprobando Redis',
    redis_check_success: 'Redis disponible',
    redis_check_error: 'Redis no disponible',
    redis_seen_result: 'Cache de vistos evaluada',
    redis_seen_marked: 'Vistos marcados en Redis',
    redis_candidate_state_pending: 'Transicion de candidatos preparada',
    redis_candidate_state_pending_error: 'Error preparando transicion de candidatos',
    redis_candidate_state_updated: 'Estado de candidatos actualizado',
    redis_candidate_state_reconciled: 'Estado de candidatos reconciliado',
    candidate_seen_skipped: 'Candidatos ya vistos omitidos',
    catalog_search_start: 'Iniciando busqueda',
    catalog_search_success: 'Busqueda completada',
    catalog_candidates_received: 'Candidatos recibidos',
    anonymous_session_bootstrap_start: 'Obteniendo sesion anonima',
    anonymous_session_bootstrap_success: 'Sesion anonima obtenida',
    anonymous_session_bootstrap_error: 'Error obteniendo sesion anonima',
    anonymous_session_refresh_start: 'Refrescando sesion anonima',
    catalog_session_context_ready: 'Contexto de catalogo listo',
    catalog_session_context_incomplete: 'Contexto de catalogo incompleto',
    vinted_session_prepare_start: 'Preparando sesion Vinted',
    vinted_session_prepare_result: 'Sesion Vinted preparada',
    detail_probe_finished: 'Probe de detalle completado',
    catalog_api_probe_start: 'Probando API de catalogo',
    catalog_api_probe_success: 'Probe API aceptado',
    catalog_api_probe_failed: 'Probe API fallido',
    catalog_api_probe_error: 'Error en probe API',
    datadome_tags_request_start: 'Cargando tags DataDome',
    datadome_tags_request_success: 'Tags DataDome cargados',
    datadome_tags_request_error: 'Error cargando tags DataDome',
    datadome_collector_start: 'DataDome evaluado',
    datadome_collector_attempt_start: 'Intento DataDome iniciado',
    datadome_collector_attempt_success: 'Intento DataDome completado',
    datadome_collector_attempt_failed: 'Intento DataDome fallido',
    datadome_collector_success: 'Recolector DataDome completado',
    datadome_collector_failed: 'DataDome omitido',
    datadome_collector_skipped: 'Recolector DataDome omitido',
    navigation_home_request_start: 'Visitando home',
    navigation_home_request_success: 'Home respondio',
    navigation_home_request_error: 'Error visitando home',
    navigation_delay_applied: 'Pausa de navegacion aplicada',
    human_delay_applied: 'Pausa humana aplicada',
    catalog_api_request_start: 'Consultando API de catalogo',
    catalog_api_request_success: 'API de catalogo respondio',
    catalog_api_request_error: 'Error en API de catalogo',
    catalog_api_session_rejected: 'Sesion rechazada por catalogo',
    catalog_api_parse_error: 'Respuesta de catalogo no procesable',
    datadome_challenge_detected: 'Challenge DataDome detectado',
    candidate_evaluation_start: 'Evaluando candidato',
    candidate_existing_opportunity_skipped: 'Oportunidad existente omitida',
    candidate_detail_required: 'Detalle requerido',
    candidate_detail_not_required: 'Detalle no requerido',
    candidate_filter_decision: 'Decision de filtros',
    detail_http_request_start: 'HTTP detalle iniciado',
    detail_http_request_success: 'HTTP detalle completado',
    detail_http_request_error: 'HTTP detalle fallido',
    detail_parse_success: 'Detalle procesado',
    detail_parse_error: 'Error procesando detalle',
    detail_batch_started: 'Lote de detalles iniciado',
    detail_batch_finished: 'Lote de detalles completado',
    detail_batch_canary_failed: 'Canario de detalles fallido',
    detail_early_filter_shadow: 'Prefiltro temprano observado',
    detail_early_filter_enforced: 'Detalle descartado durante descarga',
    detail_fetch_start: 'Obteniendo detalle',
    detail_fetch_joined: 'Resultado concurrente incorporado',
    detail_fetch_success: 'Detalle obtenido',
    detail_fetch_error: 'Error obteniendo detalle',
    detail_fetch_skipped: 'Detalle omitido',
    detail_fetch_early_discard: 'Detalle descartado por prefiltro',
    detail_candidates_claimed: 'Candidatos de detalle reclamados',
    detail_candidate_batch_closed: 'Lote de detalle descartado',
    detail_candidate_lock_expiry_pending: 'Locks de detalle pendientes de expirar',
    detail_candidate_recovery_staged: 'Recuperacion de detalle preparada',
    detail_retry_claimed: 'Reintento de detalle reclamado',
    detail_retry_scheduled: 'Reintento de detalle programado',
    detail_retry_batch_preserved: 'Lote de reintentos preservado',
    detail_retry_exhausted: 'Reintentos de detalle agotados',
    detail_incomplete: 'Detalle obligatorio incompleto',
    filter_passed: 'Filtros superados',
    item_persisted: 'Item persistido',
    item_reused: 'Item reutilizado',
    item_detail_persisted: 'Detalle persistido',
    item_discarded: 'Item descartado',
    opportunity_created: 'Oportunidad creada',
    opportunity_skipped_missing_detail: 'Oportunidad omitida sin detalle',
    opportunity_skipped_incomplete_detail: 'Oportunidad omitida por detalle incompleto',
    opportunity_skipped: 'Oportunidad ya existente',
    candidate_persistence_finished: 'Persistencia de candidato completada',
    monitor_session_closed: 'Sesion de monitor cerrada'
  };
  return labels[phase] ?? phase.replaceAll('_', ' ');
}

function triggerLabel(trigger: string): string {
  const labels: Record<string, string> = {
    manual: 'puntual',
    scheduler: 'scheduler',
    baseline: 'snapshot inicial',
    session_prepare: 'preparar sesion',
    detail_probe: 'probe detalle'
  };
  return labels[trigger] ?? trigger;
}

function proxyLabel(metadata: Record<string, unknown>): string {
  if (typeof metadata.proxy_name === 'string' && metadata.proxy_name) {
    return typeof metadata.proxy_kind === 'string' && metadata.proxy_kind ? `${metadata.proxy_name} (${metadata.proxy_kind})` : metadata.proxy_name;
  }
  if (typeof metadata.proxy_profile_id === 'number') {
    return `Perfil #${metadata.proxy_profile_id}`;
  }
  return metadata.egress_mode === 'direct' ? 'Directo' : 'Sin egress registrado';
}

function formatDuration(startedAt: string, finishedAt: string | null): string {
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  const seconds = Math.max(Math.round((end - new Date(startedAt).getTime()) / 1000), 0);
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}
