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
  const hasDetails = Object.keys(event.details).length > 0;
  const tokens = eventLineTokens(event, showRunId);
  return (
    <article className={`run-event-entry ${event.level}`}>
      <div className="event-console-line">
        <span className="event-time">{formatLogTimestamp(event.created_at)}</span>
        <span className={`event-level ${event.level}`}>{event.level.toUpperCase()}</span>
        {tokens.map((token) => (
          <span className="event-token" key={token}>{token}</span>
        ))}
        <strong>{eventLabel(event.phase)}</strong>
        {event.message ? <span className="event-message">{event.message}</span> : null}
        {event.url ? <code className="event-url">{event.url}</code> : null}
        {hasDetails ? (
          <details className="event-details">
            <summary>json</summary>
            <code>{JSON.stringify(event.details, null, 2)}</code>
          </details>
        ) : null}
      </div>
    </article>
  );
}

export function eventSearchText(event: RunEvent): string {
  return [
    event.phase,
    event.level,
    event.message,
    event.url,
    event.run_id ? `run#${event.run_id}` : null,
    eventLineTokens(event, true).join(' '),
    JSON.stringify(event.details)
  ].filter(Boolean).join(' ').toLowerCase();
}

function eventLineTokens(event: RunEvent, showRunId: boolean): string[] {
  const parts: string[] = [];
  if (showRunId && event.run_id) {
    parts.push(`run#${event.run_id}`);
  }
  if (event.method) {
    parts.push(event.method);
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
  const itemId = detailString(event.details, 'vinted_item_id');
  if (itemId) {
    parts.push(`item=${itemId}`);
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
  appendBooleanToken(parts, 'csrf', event.details, 'csrf_token_found');
  appendBooleanToken(parts, 'anon', event.details, 'anon_id_found');
  appendBooleanToken(parts, 'access', event.details, 'access_token_found');
  appendBooleanToken(parts, 'datadome', event.details, 'datadome_cookie');
  appendBooleanToken(parts, 'v_udt', event.details, 'v_udt_found');
  appendBooleanToken(parts, 'geo', event.details, 'egress_country_match');
  appendBooleanToken(parts, 'locale_ok', event.details, 'locale_configured');
  appendBooleanToken(parts, 'screen_ok', event.details, 'screen_configured');
  const locale = detailString(event.details, 'locale');
  if (locale) {
    parts.push(`locale=${locale}`);
  }
  const screen = detailString(event.details, 'screen');
  if (screen) {
    parts.push(`screen=${screen}`);
  }
  if (event.phase === 'catalog_candidates_received') {
    appendNumberToken(parts, 'items', event.details, 'candidate_count');
    appendNumberToken(parts, 'unique', event.details, 'unique_candidate_count');
    appendNumberToken(parts, 'total', event.details, 'total_entries');
  }
  if (event.phase === 'baseline_snapshot_seeded' || event.phase === 'redis_seen_marked') {
    appendNumberToken(parts, 'marked', event.details, 'marked_seen_count');
  }
  if (event.phase === 'redis_seen_result') {
    appendNumberToken(parts, 'seen', event.details, 'seen_hit_count');
    appendNumberToken(parts, 'new', event.details, 'seen_miss_count');
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

function nestedDetailString(details: Record<string, unknown>, parent: string, key: string): string | null {
  const value = details[parent];
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return detailString(value as Record<string, unknown>, key);
}

function formatLogTimestamp(value: string): string {
  const date = new Date(value);
  return new Intl.DateTimeFormat('es-ES', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3,
    hour12: false
  }).format(date);
}

function streamLabel(status: 'connecting' | 'connected' | 'error'): string {
  if (status === 'connected') {
    return 'Logs en vivo';
  }
  if (status === 'error') {
    return 'Stream no disponible; abre logs para recargar';
  }
  return 'Conectando stream';
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
    egress_diagnostic_error: 'Error diagnosticando IP',
    redis_check_start: 'Comprobando Redis',
    redis_check_success: 'Redis disponible',
    redis_check_error: 'Redis no disponible',
    redis_seen_result: 'Cache de vistos evaluada',
    redis_seen_marked: 'Vistos marcados en Redis',
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
    datadome_collector_start: 'Recolector DataDome iniciado',
    datadome_collector_success: 'Recolector DataDome completado',
    datadome_collector_failed: 'Recolector DataDome fallido',
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
    detail_fetch_start: 'Obteniendo detalle',
    detail_fetch_success: 'Detalle obtenido',
    detail_fetch_error: 'Error obteniendo detalle',
    detail_fetch_skipped: 'Detalle omitido',
    filter_passed: 'Filtros superados',
    item_persisted: 'Item persistido',
    item_reused: 'Item reutilizado',
    item_detail_persisted: 'Detalle persistido',
    item_detail_error_recorded: 'Error de detalle persistido',
    item_discarded: 'Item descartado',
    opportunity_created: 'Oportunidad creada',
    opportunity_skipped: 'Oportunidad ya existente',
    monitor_session_closed: 'Sesion de monitor cerrada'
  };
  return labels[phase] ?? phase.replaceAll('_', ' ');
}

function triggerLabel(trigger: string): string {
  const labels: Record<string, string> = {
    manual: 'puntual',
    scheduler: 'scheduler',
    baseline: 'snapshot inicial',
    session_prepare: 'preparar sesion'
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
