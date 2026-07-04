import { FileText } from 'lucide-react';
import { useState } from 'react';
import type { Run, RunEvent } from '../../api';
import { formatDate } from '../../utils/format';

export function RunsView({
  getSourceName,
  runs,
  onLoadRunEvents
}: {
  getSourceName: (sourceId: number) => string;
  runs: Run[];
  onLoadRunEvents: (runId: number) => Promise<RunEvent[]>;
}) {
  const [openRunId, setOpenRunId] = useState<number | null>(null);
  const [eventsByRunId, setEventsByRunId] = useState<Record<number, RunEvent[]>>({});
  const [loadingRunId, setLoadingRunId] = useState<number | null>(null);

  async function toggleLogs(runId: number) {
    if (openRunId === runId) {
      setOpenRunId(null);
      return;
    }
    setOpenRunId(runId);
    if (eventsByRunId[runId]) {
      return;
    }
    setLoadingRunId(runId);
    try {
      const events = await onLoadRunEvents(runId);
      setEventsByRunId((current) => ({ ...current, [runId]: events }));
    } finally {
      setLoadingRunId(null);
    }
  }

  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Monitor</h3>
        <span>{runs.length}</span>
      </div>
      {runs.length === 0 ? (
        <p className="empty-inline">Sin ejecuciones registradas.</p>
      ) : (
        <div className="monitor-grid">
          {runs.map((run) => {
            const events = eventsByRunId[run.id] ?? [];
            return (
              <article className="monitor-card" key={run.id}>
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
                <button type="button" onClick={() => void toggleLogs(run.id)}>
                  <FileText size={16} />
                  {openRunId === run.id ? 'Cerrar logs' : 'Abrir logs'}
                </button>
                {openRunId === run.id ? (
                  <div className="run-events">
                    {loadingRunId === run.id ? <p>Cargando logs...</p> : null}
                    {!loadingRunId && events.length === 0 ? <p>Sin eventos para este run.</p> : null}
                    {events.map((event) => (
                      <article key={event.id}>
                        <strong>{event.phase}</strong>
                        <span>{formatDate(event.created_at)}</span>
                        <span>{eventMeta(event)}</span>
                        {event.url ? <code>{event.url}</code> : null}
                        {event.message ? <p>{event.message}</p> : null}
                        {Object.keys(event.details).length > 0 ? <code>{JSON.stringify(event.details)}</code> : null}
                      </article>
                    ))}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </section>
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

function proxyLabel(metadata: Record<string, unknown>): string {
  if (typeof metadata.proxy_name === 'string' && metadata.proxy_name) {
    return metadata.proxy_name;
  }
  if (typeof metadata.proxy_profile_id === 'number') {
    return `Perfil #${metadata.proxy_profile_id}`;
  }
  return 'Directo / .env';
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
