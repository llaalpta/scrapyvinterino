import { AlertTriangle, Clock3, FileText, Info, RefreshCw, XCircle } from 'lucide-react';
import type { ReactNode } from 'react';
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
                  Run #{run.id} - {run.trigger}
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

function RunEventEntry({ event }: { event: RunEvent }) {
  const hasDetails = Object.keys(event.details).length > 0;
  return (
    <article className={`run-event-entry ${event.level}`}>
      <div className="event-rail">
        {eventIcon(event.level)}
      </div>
      <div className="event-body">
        <div className="event-title-row">
          <strong>{eventLabel(event.phase)}</strong>
          <span className={`event-level ${event.level}`}>{levelLabel(event.level)}</span>
        </div>
        <div className="event-meta-row">
          <span>{formatDate(event.created_at)}</span>
          {eventMeta(event) ? <span>{eventMeta(event)}</span> : null}
          {event.auth_mode ? <span>{event.auth_mode}</span> : null}
        </div>
        {event.url ? <code className="event-url">{event.url}</code> : null}
        {event.message ? <p>{event.message}</p> : null}
        {hasDetails ? (
          <details className="event-details">
            <summary>Detalles</summary>
            <code>{JSON.stringify(event.details, null, 2)}</code>
          </details>
        ) : null}
      </div>
    </article>
  );
}

function eventMeta(event: RunEvent): string {
  const parts = [];
  if (event.method) {
    parts.push(event.method);
  }
  if (event.status_code) {
    parts.push(String(event.status_code));
  }
  if (event.duration_ms !== null) {
    parts.push(`${event.duration_ms}ms`);
  }
  return parts.join(' - ');
}

function eventIcon(level: RunEvent['level']): ReactNode {
  if (level === 'error') {
    return <XCircle size={16} />;
  }
  if (level === 'warning') {
    return <AlertTriangle size={16} />;
  }
  if (level === 'debug') {
    return <Clock3 size={16} />;
  }
  return <Info size={16} />;
}

function levelLabel(level: RunEvent['level']): string {
  const labels = {
    debug: 'Debug',
    info: 'Info',
    warning: 'Aviso',
    error: 'Error'
  };
  return labels[level];
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
    run_succeeded: 'Run completado',
    run_failed: 'Run fallido',
    redis_check_start: 'Comprobando Redis',
    redis_check_success: 'Redis disponible',
    redis_check_error: 'Redis no disponible',
    redis_seen_result: 'Cache de vistos evaluada',
    catalog_search_start: 'Iniciando busqueda',
    catalog_search_success: 'Busqueda completada',
    anonymous_session_bootstrap_start: 'Obteniendo sesion anonima',
    anonymous_session_bootstrap_success: 'Sesion anonima obtenida',
    anonymous_session_bootstrap_error: 'Error obteniendo sesion anonima',
    anonymous_session_refresh_start: 'Refrescando sesion anonima',
    catalog_api_request_start: 'Consultando API de catalogo',
    catalog_api_request_success: 'API de catalogo respondio',
    catalog_api_request_error: 'Error en API de catalogo',
    catalog_api_session_rejected: 'Sesion rechazada por catalogo',
    catalog_api_parse_error: 'Respuesta de catalogo no procesable',
    detail_fetch_start: 'Obteniendo detalle',
    detail_fetch_success: 'Detalle obtenido',
    detail_fetch_error: 'Error obteniendo detalle',
    detail_fetch_skipped: 'Detalle omitido',
    filter_passed: 'Filtros superados',
    item_discarded: 'Item descartado',
    opportunity_created: 'Oportunidad creada',
    opportunity_skipped: 'Oportunidad ya existente'
  };
  return labels[phase] ?? phase.replaceAll('_', ' ');
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
