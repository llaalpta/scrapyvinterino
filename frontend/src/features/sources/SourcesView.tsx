import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Eraser, FileText, KeyRound, Play, RefreshCw, Save, Search, Square, Trash2 } from 'lucide-react';
import {
  type MonitorStats,
  type MonitorStatsRange,
  type Run,
  type RunEvent,
  type SearchSource,
  type VintedSession,
  type VintedSessionUnusableReason
} from '../../api';
import { formatDate } from '../../utils/format';
import { eventSearchText } from '../runs/runEventSearch';
import { RunEventEntry } from '../runs/RunsView';
import { buildSourceDraft, filterTermLabelFromDraft, filterTermLabelFromSource, sourceDraftHasChanges, type SourceDraft } from './sourceDrafts';

const MonitorPerformanceChart = lazy(() => import('./MonitorPerformanceChart'));

export function SourcesView({
  creatingSource,
  detailProbeMessages,
  detailProbeRefs,
  monitorEventHistoryLoadedBySource,
  monitorEventsBySource,
  monitorHiddenEventIdsBySource,
  monitorCommandPending,
  monitorRunsBySource,
  pendingStopSourceIds,
  monitorStatsBySource,
  monitorStatsRangeBySource,
  onClearMonitorEventsView,
  onCreateSource,
  onDeleteSource,
  onLoadMonitorEvents,
  onLoadMonitorRuns,
  onLoadMonitorStats,
  onPrepareVintedSession,
  onProbeItemDetail,
  onRunNow,
  onSaveSourceSchedule,
  onStartSession,
  onStopMonitor,
  runningSessionId,
  savingSourceId,
  sourceDrafts,
  sourceName,
  sources,
  sourceUrl,
  streamStatus,
  streamReady,
  setSourceName,
  setSourceUrl,
  updateDetailProbeRef,
  updateSourceDraft,
}: {
  creatingSource: boolean;
  detailProbeMessages: Record<number, string>;
  detailProbeRefs: Record<number, string>;
  monitorEventHistoryLoadedBySource: Record<number, boolean>;
  monitorEventsBySource: Record<number, RunEvent[]>;
  monitorHiddenEventIdsBySource: Record<number, number[]>;
  monitorCommandPending: boolean;
  monitorRunsBySource: Record<number, Run[]>;
  pendingStopSourceIds: number[];
  monitorStatsBySource: Record<number, MonitorStats>;
  monitorStatsRangeBySource: Record<number, MonitorStatsRange>;
  onClearMonitorEventsView: (sourceId: number, visibleEventIds: number[]) => void;
  onCreateSource: (event: FormEvent<HTMLFormElement>) => void;
  onDeleteSource: (source: SearchSource) => void;
  onLoadMonitorEvents: (sourceId: number) => Promise<void>;
  onLoadMonitorRuns: (sourceId: number) => Promise<void>;
  onLoadMonitorStats: (sourceId: number, range: MonitorStatsRange) => void;
  onPrepareVintedSession: (source: SearchSource) => void;
  onProbeItemDetail: (source: SearchSource) => void;
  onRunNow: (source: SearchSource) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  onStopMonitor: (sourceId: number) => void;
  runningSessionId: number | null;
  savingSourceId: number | null;
  sourceDrafts: Record<number, SourceDraft>;
  sourceName: string;
  sources: SearchSource[];
  sourceUrl: string;
  streamStatus: 'connecting' | 'connected' | 'error';
  streamReady: boolean;
  setSourceName: (value: string) => void;
  setSourceUrl: (value: string) => void;
  updateDetailProbeRef: (sourceId: number, value: string) => void;
  updateSourceDraft: (sourceId: number, field: keyof SourceDraft, value: string) => void;
}) {
  const drainingSourceIds = useMemo(
    () => new Set(
      sources
        .filter((source) => (
          !source.is_active
          && (pendingStopSourceIds.includes(source.id) || hasNonTerminalSessionRun(monitorRunsBySource[source.id] ?? []))
        ))
        .map((source) => source.id)
    ),
    [monitorRunsBySource, pendingStopSourceIds, sources]
  );
  const activeSources = useMemo(
    () => sources.filter((source) => source.is_active || drainingSourceIds.has(source.id)),
    [drainingSourceIds, sources]
  );
  const inactiveSources = useMemo(
    () => sources.filter((source) => !source.is_active && !drainingSourceIds.has(source.id)),
    [drainingSourceIds, sources]
  );
  const orderedSources = useMemo(() => [...activeSources, ...inactiveSources], [activeSources, inactiveSources]);
  const defaultSelectedMonitorId = activeSources[0]?.id ?? inactiveSources[0]?.id ?? null;
  const [requestedSelectedMonitorId, setSelectedMonitorId] = useState<number | null>(null);
  const selectedMonitorId = sources.some((source) => source.id === requestedSelectedMonitorId)
    ? requestedSelectedMonitorId
    : defaultSelectedMonitorId;
  const selectedSource = useMemo(
    () => sources.find((source) => source.id === selectedMonitorId) ?? null,
    [selectedMonitorId, sources]
  );
  const loadingStatsRef = useRef<Set<string>>(new Set());
  const loadingRunsRef = useRef<Set<number>>(new Set());
  const loadingEventsRef = useRef<Set<number>>(new Set());
  const detailRef = useRef<HTMLElement | null>(null);
  const [loadingMonitorEventsBySource, setLoadingMonitorEventsBySource] = useState<Record<number, boolean>>({});

  useEffect(() => {
    if (!selectedSource) {
      return;
    }
    const range = monitorStatsRangeBySource[selectedSource.id] ?? 'all';
    const loadingKey = `${selectedSource.id}:${range}`;
    if (monitorStatsBySource[selectedSource.id]) {
      loadingStatsRef.current.delete(loadingKey);
      return;
    }
    if (loadingStatsRef.current.has(loadingKey)) {
      return;
    }
    loadingStatsRef.current.add(loadingKey);
    onLoadMonitorStats(selectedSource.id, range);
  }, [monitorStatsBySource, monitorStatsRangeBySource, onLoadMonitorStats, selectedSource]);

  useEffect(() => {
    if (!selectedSource) {
      return;
    }
    if (monitorRunsBySource[selectedSource.id]) {
      loadingRunsRef.current.delete(selectedSource.id);
      return;
    }
    if (loadingRunsRef.current.has(selectedSource.id)) {
      return;
    }
    loadingRunsRef.current.add(selectedSource.id);
    void onLoadMonitorRuns(selectedSource.id).catch(() => undefined);
  }, [monitorRunsBySource, onLoadMonitorRuns, selectedSource]);

  useEffect(() => {
    if (!selectedSource || !streamReady) {
      return;
    }
    if (monitorEventHistoryLoadedBySource[selectedSource.id]) {
      loadingEventsRef.current.delete(selectedSource.id);
      return;
    }
    if (loadingEventsRef.current.has(selectedSource.id)) {
      return;
    }
    loadingEventsRef.current.add(selectedSource.id);
    setLoadingMonitorEventsBySource((current) => ({ ...current, [selectedSource.id]: true }));
    onLoadMonitorEvents(selectedSource.id).finally(() => {
      loadingEventsRef.current.delete(selectedSource.id);
      setLoadingMonitorEventsBySource((current) => ({ ...current, [selectedSource.id]: false }));
    });
  }, [monitorEventHistoryLoadedBySource, onLoadMonitorEvents, selectedSource, streamReady]);

  useEffect(() => {
    if (requestedSelectedMonitorId === null || !window.matchMedia('(max-width: 820px)').matches) {
      return;
    }
    window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0);
  }, [requestedSelectedMonitorId]);

  return (
    <section className="sources-panel monitor-page">
      <section className="monitor-page-card monitor-create-card" aria-label="Configurar nuevo monitor">
        <div className="monitor-section-heading">
          <div>
            <h3>Nuevo monitor</h3>
            <p>Guarda una URL publica de catalogo Vinted para ejecutarla de forma puntual o continua.</p>
          </div>
          <span>{sources.length} configurados</span>
        </div>
        <form className="source-form" onSubmit={onCreateSource}>
          <input disabled={monitorCommandPending} value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="Nombre del monitor" required />
          <input disabled={monitorCommandPending} value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="URL de catalogo Vinted" required />
          <button disabled={monitorCommandPending} type="submit">{creatingSource ? 'Guardando...' : 'Guardar URL'}</button>
        </form>
      </section>

      <MonitorTable
        drainingSourceIds={drainingSourceIds}
        monitorStatsBySource={monitorStatsBySource}
        selectedMonitorId={selectedMonitorId}
        sources={orderedSources}
        sourceDrafts={sourceDrafts}
        onSelectMonitor={setSelectedMonitorId}
      />

      <section className="monitor-page-card monitor-detail-shell" ref={detailRef} aria-label="Detalle del monitor seleccionado">
        <div className="monitor-section-heading compact">
          <div>
            <h3>Detalle del monitor seleccionado</h3>
            <p>{selectedSource ? 'Configuracion, rendimiento y logs acumulados.' : 'Selecciona un monitor del listado para ver su detalle.'}</p>
          </div>
          {selectedSource ? (
            <span>{drainingSourceIds.has(selectedSource.id) ? 'Deteniendo...' : selectedSource.is_active ? 'Activo' : 'Inactivo'}</span>
          ) : <span>Sin seleccion</span>}
        </div>
        <MonitorDetail
          detailProbeMessage={selectedSource ? (detailProbeMessages[selectedSource.id] ?? '') : ''}
          detailProbeRef={selectedSource ? (detailProbeRefs[selectedSource.id] ?? '') : ''}
          hiddenEventIds={selectedSource ? (monitorHiddenEventIdsBySource[selectedSource.id] ?? []) : []}
          isDraining={selectedSource ? drainingSourceIds.has(selectedSource.id) : false}
          loadingMonitorEvents={selectedSource ? Boolean(loadingMonitorEventsBySource[selectedSource.id]) : false}
          monitorEvents={selectedSource ? (monitorEventsBySource[selectedSource.id] ?? []) : []}
          monitorRuns={selectedSource ? (monitorRunsBySource[selectedSource.id] ?? []) : []}
          monitorRunStateKnown={selectedSource ? Object.hasOwn(monitorRunsBySource, selectedSource.id) : false}
          monitorCommandPending={monitorCommandPending}
          onClearMonitorEventsView={onClearMonitorEventsView}
          onDeleteSource={onDeleteSource}
          onLoadMonitorStats={onLoadMonitorStats}
          onPrepareVintedSession={onPrepareVintedSession}
          onProbeItemDetail={onProbeItemDetail}
          onRunNow={onRunNow}
          onSaveSourceSchedule={onSaveSourceSchedule}
          onStartSession={onStartSession}
          onStopMonitor={onStopMonitor}
          runningSessionId={runningSessionId}
          savingSourceId={savingSourceId}
          source={selectedSource}
          sourceDrafts={sourceDrafts}
          stats={selectedSource ? (monitorStatsBySource[selectedSource.id] ?? null) : null}
          statsRange={selectedSource ? (monitorStatsRangeBySource[selectedSource.id] ?? 'all') : 'all'}
          streamStatus={streamStatus}
          updateDetailProbeRef={updateDetailProbeRef}
          updateSourceDraft={updateSourceDraft}
        />
      </section>
    </section>
  );
}

function MonitorPerformancePanel({
  onRangeChange,
  range,
  stats
}: {
  onRangeChange: (range: MonitorStatsRange) => void;
  range: MonitorStatsRange;
  stats: MonitorStats | null;
}) {
  const baseChartData = (stats?.chart_points ?? []).map((point) => ({
    bucketEndMs: new Date(point.bucket_end).getTime(),
    bucketStartMs: new Date(point.bucket_start).getTime(),
    itemsFound: point.items_found,
    runsCount: point.runs_count
  }));
  const chartData = baseChartData;
  const chartRange =
    stats !== null
      ? {
          bucketLabel: stats.bucket_label,
          bucketSeconds: stats.bucket_seconds,
          rangeEndMs: new Date(stats.range_end).getTime(),
          rangeStartMs: new Date(stats.range_start).getTime()
        }
      : null;
  const chartDomain = chartRange ? ([chartRange.rangeStartMs, chartRange.rangeEndMs] as [number, number]) : undefined;
  const activeSessionMs = stats?.active_session ? new Date(stats.active_session.started_at).getTime() : null;
  const historical = stats?.historical_summary;
  const hasAnySession = (historical?.sessions_count ?? 0) > 0 || Boolean(stats?.active_session ?? stats?.latest_session);

  return (
    <section className="monitor-performance">
      <div className="monitor-performance-heading">
        <div>
          <h4>Rendimiento del monitor</h4>
          <span>{hasAnySession ? 'Historico acumulado y resultados por intervalo' : 'Sin sesiones registradas'}</span>
        </div>
        <div className="range-tabs" aria-label="Rango de grafica">
          {rangeOptions.map((option) => (
            <button
              className={range === option.value ? 'active' : ''}
              key={option.value}
              type="button"
              onClick={() => onRangeChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {hasAnySession ? (
        <>
          <dl className="monitor-accumulated-strip">
            <Metric label="Sesiones" value={String(historical?.sessions_count ?? 0)} />
            <Metric label="Tiempo activo" value={formatSeconds(historical?.active_seconds ?? 0)} />
            <Metric label="Ejecuciones" value={String(historical?.runs_count ?? 0)} />
            <Metric label="Encontrados" value={String(historical?.items_found ?? 0)} />
            <Metric label="Nuevos" value={String(historical?.items_new ?? 0)} />
            <Metric label="Descartados" value={String(historical?.items_discarded_by_filters ?? 0)} />
            <Metric label="Oportunidades" value={String(historical?.opportunities_created ?? 0)} />
            <Metric label="Fallos" value={String(historical?.failed_runs ?? 0)} />
          </dl>

          <div className="monitor-chart">
            {chartData.length === 0 ? (
              <p className="empty-inline compact">Sin datos historicos para graficar.</p>
            ) : (
              <ChartErrorBoundary key={range}>
                <Suspense fallback={<div className="monitor-chart-loading" aria-hidden="true" />}>
                  <MonitorPerformanceChart
                    chartData={chartData}
                    chartDomain={chartDomain}
                    chartRange={chartRange}
                    range={range}
                    sessionStartedAtMs={activeSessionMs}
                  />
                </Suspense>
              </ChartErrorBoundary>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}

function MonitorSessionOverview({ source, stats }: { source: SearchSource; stats: MonitorStats | null }) {
  const session = stats?.active_session ?? stats?.latest_session ?? null;
  const summary = stats?.session_summary ?? null;

  if (!session) {
    return null;
  }

  const isActiveSession = Boolean(stats?.active_session);
  const endLabel = isActiveSession ? 'Duracion activa' : 'Fin';
  const endValue = isActiveSession ? formatSeconds(session.duration_seconds) : session.stopped_at ? formatDate(session.stopped_at) : '-';

  return (
    <section className="monitor-session-panel" aria-label={isActiveSession ? 'Sesion activa' : 'Ultima sesion'}>
      <div className="monitor-session-heading">
        <h4>{isActiveSession ? 'Sesion activa' : 'Ultima sesion'}</h4>
        {source.next_run_at ? <span>Proxima {formatDate(source.next_run_at)}</span> : null}
      </div>
      <dl className="monitor-session-strip">
        <Metric label="Inicio" value={formatDate(session.started_at)} />
        <Metric label={endLabel} value={endValue} />
        <Metric label="Ejecuciones" value={String(summary?.runs_count ?? 0)} />
        <Metric label="Encontrados" value={String(summary?.items_found ?? 0)} />
        <Metric label="Oportunidades" value={String(summary?.opportunities_created ?? 0)} />
        <Metric label="Errores" value={String(summary?.failed_runs ?? 0)} />
      </dl>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function MonitorEventTimeline({
  events,
  loading,
  streamStatus,
  viewCleared
}: {
  events: RunEvent[];
  loading: boolean;
  streamStatus: 'connecting' | 'connected' | 'error' | null;
  viewCleared: boolean;
}) {
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const latestEventIdRef = useRef<number | null>(null);
  const [followingTail, setFollowingTail] = useState(true);
  const [newEventCount, setNewEventCount] = useState(0);
  const orderedEvents = useMemo(
    () => [...events].sort((left, right) => left.id - right.id),
    [events]
  );
  const [levelFilter, setLevelFilter] = useState<RunEvent['level'] | 'all'>('all');
  const [searchText, setSearchText] = useState('');
  const normalizedSearch = searchText.trim().toLowerCase();
  const visibleEvents = useMemo(
    () =>
      orderedEvents.filter((event) => {
        if (levelFilter !== 'all' && event.level !== levelFilter) {
          return false;
        }
        return normalizedSearch === '' || eventSearchText(event).includes(normalizedSearch);
      }),
    [levelFilter, normalizedSearch, orderedEvents]
  );
  const filterHasMatches = visibleEvents.length > 0;
  const hasActiveFilter = levelFilter !== 'all' || normalizedSearch !== '';

  useEffect(() => {
    const latestEventId = orderedEvents.at(-1)?.id ?? null;
    const previousEventId = latestEventIdRef.current;
    latestEventIdRef.current = latestEventId;
    if (latestEventId === null) {
      return;
    }
    const appendedCount = previousEventId === null
      ? 0
      : orderedEvents.filter((event) => event.id > previousEventId).length;
    if (!followingTail && appendedCount > 0) {
      setNewEventCount((current) => current + appendedCount);
      return;
    }
    if (followingTail) {
      window.requestAnimationFrame(() => {
        const timeline = timelineRef.current;
        if (timeline) {
          timeline.scrollTop = timeline.scrollHeight;
        }
      });
    }
  }, [followingTail, orderedEvents]);

  function handleTimelineScroll() {
    const timeline = timelineRef.current;
    if (!timeline) {
      return;
    }
    const isAtTail = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight <= 32;
    setFollowingTail(isAtTail);
    if (isAtTail) {
      setNewEventCount(0);
    }
  }

  function followNewestEvents() {
    setFollowingTail(true);
    setNewEventCount(0);
    timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: 'smooth' });
  }

  return (
    <div className="run-events monitor-event-timeline" onScroll={handleTimelineScroll} ref={timelineRef}>
      <div className="event-log-toolbar">
        <div className={`event-stream-status ${streamStatus ?? 'connected'}`}>
          <RefreshCw size={14} />
          <span>{streamStatus ? monitorStreamLabel(streamStatus) : 'Historico acumulado'}</span>
        </div>
        <div className="event-log-controls">
          <select
            aria-label="Filtrar logs por nivel"
            value={levelFilter}
            onChange={(event) => setLevelFilter(event.target.value as RunEvent['level'] | 'all')}
          >
            <option value="all">Todos los niveles</option>
            <option value="info">Info</option>
            <option value="debug">Debug</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
          </select>
          <input
            aria-label="Buscar logs"
            placeholder="Buscar logs"
            type="search"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
        </div>
      </div>
      {newEventCount > 0 ? (
        <button className="new-events-button" type="button" onClick={followNewestEvents}>
          {newEventCount} {newEventCount === 1 ? 'evento nuevo' : 'eventos nuevos'}
        </button>
      ) : null}
      {loading ? <p className="event-empty">Cargando logs acumulados...</p> : null}
      {!loading && viewCleared ? <p className="event-empty">Vista limpia. Los nuevos eventos apareceran aqui.</p> : null}
      {!loading && !viewCleared && orderedEvents.length === 0 ? <p className="event-empty">Sin logs acumulados para este monitor.</p> : null}
      {!loading && !viewCleared && orderedEvents.length > 0 && hasActiveFilter && !filterHasMatches ? (
        <p className="event-empty">Sin coincidencias para el filtro actual.</p>
      ) : null}
      {visibleEvents.map((event) => (
        <RunEventEntry event={event} key={event.id} showRunId />
      ))}
    </div>
  );
}

function monitorStreamLabel(status: 'connecting' | 'connected' | 'error'): string {
  if (status === 'connected') {
    return 'Logs en vivo';
  }
  if (status === 'error') {
    return 'Reconectando logs en vivo';
  }
  return 'Conectando stream';
}

class ChartErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return <p className="empty-inline compact">Grafica no disponible en este momento.</p>;
    }
    return this.props.children;
  }
}

const rangeOptions: Array<{ label: string; value: MonitorStatsRange }> = [
  { label: 'Minuto', value: 'minutes' },
  { label: 'Hora', value: 'hours' },
  { label: 'Dia', value: 'days' },
  { label: 'Mes', value: 'month' },
  { label: 'Todo', value: 'all' }
];

function formatSeconds(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `${hours}h ${minutes % 60}m`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function MonitorTable({
  drainingSourceIds,
  monitorStatsBySource,
  onSelectMonitor,
  selectedMonitorId,
  sources,
  sourceDrafts
}: {
  drainingSourceIds: Set<number>;
  monitorStatsBySource: Record<number, MonitorStats>;
  onSelectMonitor: (sourceId: number) => void;
  selectedMonitorId: number | null;
  sources: SearchSource[];
  sourceDrafts: Record<number, SourceDraft>;
}) {
  return (
    <section className="monitor-page-card monitor-table-panel" aria-label="Monitores configurados">
      <div className="monitor-section-heading compact">
        <div>
          <h3>Monitores configurados</h3>
          <p>Activos primero; selecciona una fila para revisar o editar el monitor.</p>
        </div>
        <span>{sources.length}</span>
      </div>
      {sources.length === 0 ? (
        <p className="empty-inline compact">No hay monitores configurados.</p>
      ) : (
        <div className="monitor-table">
          <div className="monitor-table-header" aria-hidden="true">
            <span>Monitor</span>
            <span>Estado</span>
            <span>Modo</span>
            <span>Configuracion</span>
            <span>Metricas</span>
          </div>
          {sources.map((source) => {
            const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
            const isDraining = drainingSourceIds.has(source.id);
            const summary = source.is_active || isDraining ? monitorSummary(source) : draftSummary(source, draft);
            return (
              <MonitorTableRow
                isDraining={isDraining}
                isSelected={source.id === selectedMonitorId}
                key={source.id}
                source={source}
                stats={monitorStatsBySource[source.id] ?? null}
                summary={summary}
                onSelect={() => onSelectMonitor(source.id)}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}

function MonitorTableRow({
  isDraining,
  isSelected,
  onSelect,
  source,
  stats,
  summary
}: {
  isDraining: boolean;
  isSelected: boolean;
  onSelect: () => void;
  source: SearchSource;
  stats: MonitorStats | null;
  summary: string[];
}) {
  const modeEntries = summary.filter((entry) => !entry.startsWith('Filtros:')).slice(0, 3);
  const configEntries = summary.filter((entry) => entry.startsWith('Filtros:'));
  const rowClassName = [
    'monitor-table-row',
    source.is_active || isDraining ? 'is-active' : '',
    isSelected ? 'selected' : ''
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button
      className={rowClassName}
      type="button"
      aria-pressed={isSelected}
      aria-label={`${source.name}, ${isDraining ? 'deteniendo' : source.is_active ? 'activo' : 'inactivo'}`}
      onClick={onSelect}
    >
      <span className="monitor-table-cell monitor-table-main" data-label="Monitor">
        <span className="monitor-table-value">
          <strong>{source.name}</strong>
          <span>{source.url}</span>
        </span>
      </span>
      <span className="monitor-table-cell" data-label="Estado">
        <span className="monitor-table-value monitor-table-status">
          <span className={source.is_active || isDraining ? 'status running' : 'status'}>
            {isDraining ? 'Deteniendo...' : source.is_active ? 'Activo' : 'Inactivo'}
          </span>
        </span>
      </span>
      <span className="monitor-table-cell" data-label="Modo">
        <span className="monitor-table-value monitor-table-tags">
          {modeEntries.map((entry) => (
            <span key={entry}>{entry}</span>
          ))}
        </span>
      </span>
      <span className="monitor-table-cell" data-label="Configuracion">
        <span className="monitor-table-value monitor-table-tags">
          {configEntries.map((entry) => (
            <span key={entry}>{entry}</span>
          ))}
        </span>
      </span>
      <span className="monitor-table-cell" data-label="Metricas">
        <span className="monitor-table-value monitor-table-metrics">
          {monitorListMetrics(stats).map((entry) => (
            <span key={entry}>{entry}</span>
          ))}
        </span>
      </span>
    </button>
  );
}

function MonitorDetail({
  detailProbeMessage,
  detailProbeRef,
  hiddenEventIds,
  isDraining,
  loadingMonitorEvents,
  monitorEvents,
  monitorRuns,
  monitorRunStateKnown,
  monitorCommandPending,
  onClearMonitorEventsView,
  onDeleteSource,
  onLoadMonitorStats,
  onPrepareVintedSession,
  onProbeItemDetail,
  onRunNow,
  onSaveSourceSchedule,
  onStartSession,
  onStopMonitor,
  runningSessionId,
  savingSourceId,
  source,
  sourceDrafts,
  stats,
  statsRange,
  streamStatus,
  updateDetailProbeRef,
  updateSourceDraft
}: {
  detailProbeMessage: string;
  detailProbeRef: string;
  hiddenEventIds: number[];
  isDraining: boolean;
  loadingMonitorEvents: boolean;
  monitorEvents: RunEvent[];
  monitorRuns: Run[];
  monitorRunStateKnown: boolean;
  monitorCommandPending: boolean;
  onClearMonitorEventsView: (sourceId: number, visibleEventIds: number[]) => void;
  onDeleteSource: (source: SearchSource) => void;
  onLoadMonitorStats: (sourceId: number, range: MonitorStatsRange) => void;
  onPrepareVintedSession: (source: SearchSource) => void;
  onProbeItemDetail: (source: SearchSource) => void;
  onRunNow: (source: SearchSource) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  onStopMonitor: (sourceId: number) => void;
  runningSessionId: number | null;
  savingSourceId: number | null;
  source: SearchSource | null;
  sourceDrafts: Record<number, SourceDraft>;
  stats: MonitorStats | null;
  statsRange: MonitorStatsRange;
  streamStatus: 'connecting' | 'connected' | 'error';
  updateDetailProbeRef: (sourceId: number, value: string) => void;
  updateSourceDraft: (sourceId: number, field: keyof SourceDraft, value: string) => void;
}) {
  const [archiveSource, setArchiveSource] = useState<SearchSource | null>(null);
  const archiveDialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!archiveSource) {
      return;
    }
    archiveDialogRef.current?.focus();
  }, [archiveSource]);

  const hiddenEventIdSet = useMemo(() => new Set(hiddenEventIds), [hiddenEventIds]);
  const visibleMonitorEvents = useMemo(
    () => monitorEvents.filter((event) => !hiddenEventIdSet.has(event.id)),
    [hiddenEventIdSet, monitorEvents]
  );
  const logViewCleared = hiddenEventIds.length > 0 && visibleMonitorEvents.length === 0;
  const canClearLogView = !loadingMonitorEvents && visibleMonitorEvents.length > 0;
  const historicalRunCountLabel = `${stats?.historical_summary.runs_count ?? monitorRuns.length} ejec.`;

  if (!source) {
    return <p className="empty-inline compact">Selecciona un monitor para ver el detalle.</p>;
  }

  const sourceDraft = sourceDrafts[source.id] ?? buildSourceDraft(source);
  const hasUnsavedChanges = sourceDraftHasChanges(source, sourceDraft);
  const isManual = source.monitor_mode === 'manual';
  const launchBlockedByFilters = source.catalog_filter_compatibility ? !source.catalog_filter_compatibility.compatible : false;
  const hasNonTerminalRun = monitorRuns.some((run) => run.status === 'running' || run.status === 'finalizing');
  const isRunStateUnknown = !source.is_active && !monitorRunStateKnown;
  const hasCommandInFlight = monitorCommandPending || hasNonTerminalRun || isRunStateUnknown;
  const detailProbeBlocked = savingSourceId === source.id || hasCommandInFlight || hasUnsavedChanges || launchBlockedByFilters || detailProbeRef.trim() === '';

  return (
    <div className={`monitor-detail-content${source.is_active || isDraining ? ' active-monitor-detail' : ' inactive-monitor-detail'}`}>
      <div className="source-card-header">
        <div className="source-main">
          <strong>{source.name}</strong>
          <a href={source.url} target="_blank" rel="noreferrer">
            {source.url}
          </a>
        </div>
        <div className="source-badges">
          <span className={source.is_active || isDraining ? 'status running' : 'status'}>
            {isDraining ? 'Deteniendo...' : source.is_active ? 'Activo' : 'Inactivo'}
          </span>
        </div>
      </div>

      <MonitorSessionOverview source={source} stats={stats} />

      <PreparedSessionsPanel sessions={source.prepared_sessions} />

      <section className={`monitor-config-panel${source.is_active || isDraining || isRunStateUnknown ? ' readonly' : ''}`}>
        <div className="monitor-config-heading">
          <h4>Configuracion</h4>
          {source.is_active || hasCommandInFlight ? (
            <span>
              {isDraining
                ? 'Deteniendo la sesion; espera a que termine la ejecucion.'
                : source.is_active
                  ? 'Deten el monitor para editarla.'
                  : isRunStateUnknown
                    ? 'Comprobando el estado de ejecucion antes de habilitar acciones.'
                    : 'Espera a que termine el comando de monitor en curso.'}
            </span>
          ) : (
            <span>Editable con el monitor detenido.</span>
          )}
        </div>
        <CatalogFilterCompatibilityStatus source={source} />
        {!source.is_active && !isDraining ? (
          <div className="detail-probe-panel">
            <div className="detail-probe-heading">
              <strong>Detalle de item</strong>
              <span>HTML / Next Flight</span>
            </div>
            <div className="detail-probe-controls">
              <input
                aria-label="ID o URL de item para probar detalle"
                disabled={savingSourceId === source.id || hasCommandInFlight}
                placeholder="ID o URL de item Vinted"
                value={detailProbeRef}
                onChange={(event) => updateDetailProbeRef(source.id, event.target.value)}
              />
              <button
                type="button"
                disabled={detailProbeBlocked}
                title={
                  hasUnsavedChanges
                    ? 'Guarda los cambios antes de probar detalle'
                    : launchBlockedByFilters
                      ? 'Corrige los filtros de URL no soportados antes de probar detalle'
                      : detailProbeRef.trim() === ''
                        ? 'Introduce un ID o URL de item Vinted'
                        : 'Probar detalle'
                }
                onClick={() => onProbeItemDetail(source)}
              >
                <Search size={16} />
                Probar detalle
              </button>
            </div>
            {detailProbeMessage ? <p className="detail-probe-message">{detailProbeMessage}</p> : null}
          </div>
        ) : null}
        <MonitorConfigEditor
          disabled={source.is_active || hasCommandInFlight}
          source={source}
          sourceDraft={sourceDraft}
          updateSourceDraft={updateSourceDraft}
        />
        <div className="monitor-config-actions">
          {source.is_active ? (
            <>
              {isManual ? (
                <button type="button" disabled={savingSourceId === source.id || hasCommandInFlight} onClick={() => onRunNow(source)}>
                  <Play size={16} />
                  {runningSessionId === source.id ? 'Ejecutando...' : 'Ejecutar ahora'}
                </button>
              ) : null}
              <button
                type="button"
                disabled={monitorCommandPending}
                title={monitorCommandPending ? 'Hay un comando de monitor en curso' : 'Detener sesion'}
                onClick={() => onStopMonitor(source.id)}
              >
                <Square size={16} />
                Detener sesion
              </button>
            </>
          ) : (
            <>
              <button type="button" disabled={savingSourceId === source.id || hasCommandInFlight || !hasUnsavedChanges} title="Guardar monitor" onClick={() => onSaveSourceSchedule(source)}>
                <Save size={16} />
                Guardar
              </button>
              <button
                type="button"
                disabled={savingSourceId === source.id || hasCommandInFlight || hasUnsavedChanges || launchBlockedByFilters}
                title={
                  hasUnsavedChanges
                    ? 'Guarda los cambios antes de preparar la sesion Vinted'
                    : launchBlockedByFilters
                      ? 'Corrige los filtros de URL no soportados antes de preparar la sesion Vinted'
                      : 'Preparar y probar sesion Vinted'
                }
                onClick={() => onPrepareVintedSession(source)}
              >
                <KeyRound size={16} />
                Preparar sesion
              </button>
              <button
                type="button"
                disabled={hasCommandInFlight || savingSourceId === source.id || hasUnsavedChanges || launchBlockedByFilters}
                title={
                  hasUnsavedChanges
                    ? 'Guarda los cambios antes de iniciar la sesion'
                    : launchBlockedByFilters
                      ? 'Corrige los filtros de URL no soportados antes de ejecutar este monitor'
                      : isManual
                        ? 'Iniciar sesion y calibrar el listado actual'
                        : 'Iniciar sesion periodica y calibrar el listado actual'
                }
                onClick={() => onStartSession(source)}
              >
                <Play size={17} />
                {runningSessionId === source.id ? 'Iniciando...' : 'Iniciar sesion'}
              </button>
              <button
                className="danger-button"
                type="button"
                disabled={savingSourceId === source.id || hasCommandInFlight}
                title="Archivar monitor"
                onClick={() => setArchiveSource(source)}
              >
                <Trash2 size={16} />
                Archivar monitor
              </button>
              {hasUnsavedChanges ? <span className="monitor-config-dirty">Cambios sin guardar</span> : null}
            </>
          )}
        </div>
      </section>

      <MonitorPerformancePanel
        range={statsRange}
        stats={stats}
        onRangeChange={(range) => onLoadMonitorStats(source.id, range)}
      />

      <section className="monitor-logs" aria-label="Logs acumulados">
        <div className="monitor-logs-header">
          <div className="monitor-logs-title">
            <FileText size={15} />
            <h4>Logs acumulados</h4>
            <span>{historicalRunCountLabel}</span>
          </div>
          <button
            type="button"
            disabled={!canClearLogView}
            title="Oculta los logs visibles sin borrar eventos guardados"
            onClick={() => onClearMonitorEventsView(source.id, visibleMonitorEvents.map((event) => event.id))}
          >
            <Eraser size={15} />
            Limpiar vista
          </button>
        </div>
        <p className="monitor-log-note">Solo oculta eventos en esta pantalla; el historico permanece guardado.</p>
        <MonitorEventTimeline
          key={source.id}
          events={visibleMonitorEvents}
          loading={loadingMonitorEvents}
          streamStatus={source.is_active || isDraining ? streamStatus : null}
          viewCleared={logViewCleared}
        />
      </section>

      {archiveSource ? (
        <div className="confirm-dialog-backdrop" role="presentation" onClick={() => setArchiveSource(null)}>
          <div
            aria-describedby="archive-monitor-description"
            aria-labelledby="archive-monitor-title"
            aria-modal="true"
            className="confirm-dialog"
            ref={archiveDialogRef}
            role="dialog"
            tabIndex={-1}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setArchiveSource(null);
              }
            }}
          >
            <h4 id="archive-monitor-title">Archivar monitor</h4>
            <p id="archive-monitor-description">
              Se archivara "{archiveSource.name}" y desaparecera de la tabla de monitores. El historico se conservara para auditoria y resultados.
            </p>
            <div className="confirm-dialog-actions">
              <button type="button" onClick={() => setArchiveSource(null)}>
                Cancelar
              </button>
              <button
                className="danger-button"
                type="button"
                disabled={monitorCommandPending}
                onClick={() => {
                  const sourceToArchive = archiveSource;
                  setArchiveSource(null);
                  onDeleteSource(sourceToArchive);
                }}
              >
                <Trash2 size={16} />
                Archivar monitor
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function hasNonTerminalSessionRun(runs: Run[]): boolean {
  return runs.some(
    (run) => run.monitor_session_id !== null && (run.status === 'running' || run.status === 'finalizing')
  );
}

function PreparedSessionsPanel({ sessions }: { sessions: VintedSession[] }) {
  const usableCount = sessions.filter((session) => session.usable_now).length;

  return (
    <section className="monitor-session-panel" aria-label="Sesiones Vinted preparadas para este monitor">
      <div className="monitor-session-heading">
        <div className="monitor-prepared-session-heading-copy">
          <h4>Sesiones Vinted preparadas</h4>
          <p>Contexto canonico reutilizable si el runtime admite ese proxy.</p>
        </div>
        <span>{sessions.length === 0 ? 'Sin contexto' : `${usableCount}/${sessions.length} utilizables`}</span>
      </div>
      {sessions.length === 0 ? (
        <p className="empty-inline compact">Sin contexto preparado para este monitor.</p>
      ) : (
        <div className="monitor-prepared-session-list">
          {sessions.map((session) => (
            <PreparedSessionRow key={session.proxy_profile_id} session={session} />
          ))}
        </div>
      )}
    </section>
  );
}

function PreparedSessionRow({ session }: { session: VintedSession }) {
  const reason = session.unusable_reason ? preparedSessionReasonLabel(session.unusable_reason) : null;

  return (
    <article className={`catalog-filter-status monitor-prepared-session-row ${session.usable_now ? 'ready' : 'blocked'}`}>
      <div className="monitor-prepared-session-main">
        <div>
          <strong>{session.proxy_name}</strong>
          <span>Sesion #{session.id} | estado durable {session.status}</span>
        </div>
        <span className={session.usable_now ? 'status active' : 'status failed'}>
          {session.usable_now ? 'Utilizable ahora' : 'No utilizable'}
        </span>
      </div>
      <p>{session.usable_now ? 'Cumple el contexto efectivo del runtime.' : reason ?? 'Motivo no disponible.'}</p>
      <dl className="monitor-session-strip">
        <Metric label="Usos" value={`${session.request_count}/${session.max_requests}`} />
        <Metric label="Expira" value={session.expires_at ? formatDate(session.expires_at) : 'Sin expiracion'} />
        <Metric label="Ultimo uso" value={session.last_used_at ? formatDate(session.last_used_at) : 'Nunca'} />
      </dl>
    </article>
  );
}

function preparedSessionReasonLabel(reason: VintedSessionUnusableReason): string {
  const labels: Record<VintedSessionUnusableReason, string> = {
    status_incomplete: 'La preparacion quedo incompleta.',
    status_invalid: 'La sesion fue invalidada.',
    status_unrecognized: 'El estado durable no es reconocido.',
    proxy_identity_mismatch: 'La identidad efectiva del proxy ha cambiado.',
    browser_profile_mismatch: 'El perfil de navegador ya no coincide.',
    request_context_mismatch: 'El contexto HTTP efectivo ya no coincide.',
    expired: 'La sesion ha expirado.',
    exhausted: 'La sesion ha agotado su limite de usos.',
    context_unreadable: 'El contexto cifrado no se puede leer.',
    context_incomplete: 'Faltan datos requeridos en el contexto preparado.'
  };
  return labels[reason];
}

function CatalogFilterCompatibilityStatus({ source }: { source: SearchSource }) {
  const compatibility = source.catalog_filter_compatibility;
  if (!compatibility) {
    return null;
  }
  const supported = Object.entries(compatibility.supported);
  const ignored = Object.entries(compatibility.ignored);
  const unsupported = Object.entries(compatibility.unsupported);
  return (
    <div className={`catalog-filter-status ${compatibility.compatible ? 'ready' : 'blocked'}`}>
      <div className="catalog-filter-status-title">
        <span>{compatibility.compatible ? 'Filtros URL compatibles' : 'Filtros URL no soportados'}</span>
      </div>
      {supported.length > 0 ? <span>Aplicados: {formatFilterEntries(supported)}</span> : <span>Sin filtros URL aplicados.</span>}
      {ignored.length > 0 ? <span>Ignorados: {formatFilterEntries(ignored)}</span> : null}
      {unsupported.length > 0 ? <strong>Bloquean: {formatFilterEntries(unsupported)}</strong> : null}
    </div>
  );
}

function formatFilterEntries(entries: Array<[string, string[]]>): string {
  return entries.map(([key, values]) => `${key}=${values.join(',') || '""'}`).join(' · ');
}

function MonitorConfigEditor({
  disabled,
  source,
  sourceDraft,
  updateSourceDraft
}: {
  disabled: boolean;
  source: SearchSource;
  sourceDraft: SourceDraft;
  updateSourceDraft: (sourceId: number, field: keyof SourceDraft, value: string) => void;
}) {
  const isRecurring = sourceDraft.monitorMode !== 'manual';

  return (
    <div className="monitor-config-editor">
      <div className="source-identity-editor compact">
        <label>
          Nombre
          <input
            disabled={disabled}
            value={sourceDraft.name}
            onChange={(event) => updateSourceDraft(source.id, 'name', event.target.value)}
          />
        </label>
        <label>
          URL de catalogo
          <input
            disabled={disabled}
            type="url"
            value={sourceDraft.url}
            onChange={(event) => updateSourceDraft(source.id, 'url', event.target.value)}
          />
        </label>
      </div>
      <div className="source-schedule compact">
        <label>
          Modo
          <select disabled={disabled} value={sourceDraft.monitorMode} onChange={(event) => updateSourceDraft(source.id, 'monitorMode', event.target.value)}>
            <option value="manual">Puntual</option>
            <option value="continuous">Continuo</option>
            <option value="duration">Durante X minutos</option>
            <option value="window">Rango horario</option>
          </select>
        </label>
        {isRecurring ? (
          <>
            <label>
              Intervalo
              <input
                type="number"
                min="60"
                max="3600"
                disabled={disabled}
                value={sourceDraft.intervalSeconds}
                onChange={(event) => updateSourceDraft(source.id, 'intervalSeconds', event.target.value)}
              />
            </label>
            <label>
              Jitter
              <input
                type="number"
                min="0"
                max="50"
                disabled={disabled}
                value={sourceDraft.jitterPercent}
                onChange={(event) => updateSourceDraft(source.id, 'jitterPercent', event.target.value)}
              />
            </label>
            <label>
              Parar tras usos sesion
              <input
                type="number"
                min="1"
                max="1000"
                disabled={disabled}
                placeholder="sin limite"
                value={sourceDraft.stopAfterVintedSessionUses}
                onChange={(event) => updateSourceDraft(source.id, 'stopAfterVintedSessionUses', event.target.value)}
              />
            </label>
          </>
        ) : null}
        {sourceDraft.monitorMode === 'window' ? (
          <>
            <label>
              Inicio
              <input
                type="time"
                disabled={disabled}
                value={sourceDraft.windowStart}
                onChange={(event) => updateSourceDraft(source.id, 'windowStart', event.target.value)}
              />
            </label>
            <label>
              Fin
              <input
                type="time"
                disabled={disabled}
                value={sourceDraft.windowEnd}
                onChange={(event) => updateSourceDraft(source.id, 'windowEnd', event.target.value)}
              />
            </label>
          </>
        ) : null}
        {sourceDraft.monitorMode === 'duration' ? (
          <label>
            Minutos
            <input
              type="number"
              min="1"
              max="1440"
              disabled={disabled}
              value={sourceDraft.sessionDurationMinutes}
              onChange={(event) => updateSourceDraft(source.id, 'sessionDurationMinutes', event.target.value)}
            />
          </label>
        ) : null}
      </div>

      <div className="source-filter-picker compact monitor-filter-editor">
        <label>
          Terminos excluyentes de la descripcion
          <textarea
            disabled={disabled}
            value={sourceDraft.filterTerms}
            rows={4}
            placeholder="manchas, roto, destenido"
            onChange={(event) => updateSourceDraft(source.id, 'filterTerms', event.target.value)}
          />
        </label>
        <span>Solo se buscan en la descripcion publica de este monitor.</span>
      </div>
    </div>
  );
}

function monitorListMetrics(stats: MonitorStats | null): string[] {
  if (!stats) {
    return ['Metricas al seleccionar'];
  }
  if (stats.historical_summary.sessions_count === 0) {
    return ['Sin sesiones'];
  }
  return [
    `${stats.historical_summary.runs_count} ejec.`,
    `${stats.historical_summary.items_found} encontrados`,
    `${stats.historical_summary.opportunities_created} oportunidades`
  ];
}

function monitorSummary(source: SearchSource): string[] {
  const config = source.scheduler_config ?? {};
  const entries = [`Modo: ${modeLabel(source.monitor_mode)}`];
  if (source.monitor_mode !== 'manual') {
    entries.push(`Cada ${config.interval_seconds ?? 300}s`);
    entries.push(`Jitter ${config.jitter_percent ?? 20}%`);
    if (config.stop_after_vinted_session_uses) {
      entries.push(`Max ${config.stop_after_vinted_session_uses} usos sesion`);
    }
  }
  if (source.monitor_mode === 'duration' && source.monitor_until) {
    entries.push(`Hasta ${formatDate(source.monitor_until)}`);
  }
  if (source.monitor_mode === 'window' && config.allowed_windows?.[0]) {
    entries.push(`Ventana ${config.allowed_windows[0]}`);
  }
  entries.push(`Filtros: ${filterTermLabelFromSource(source)}`);
  if (source.monitor_started_at) {
    entries.push(`Activo desde ${formatDate(source.monitor_started_at)}`);
  }
  if (source.last_run_at) {
    entries.push(`Ultima ${formatDate(source.last_run_at)}`);
  }
  if (source.next_run_at) {
    entries.push(`Proxima ${formatDate(source.next_run_at)}`);
  }
  return entries;
}

function draftSummary(
  source: SearchSource,
  draft: SourceDraft
): string[] {
  const entries = [`Modo: ${modeLabel(draft.monitorMode)}`];
  if (draft.monitorMode !== 'manual') {
    entries.push(`Cada ${draft.intervalSeconds || source.scheduler_config.interval_seconds || 300}s`);
    entries.push(`Jitter ${draft.jitterPercent || source.scheduler_config.jitter_percent || 20}%`);
    const stopAfterUses = draft.stopAfterVintedSessionUses || source.scheduler_config.stop_after_vinted_session_uses;
    if (stopAfterUses) {
      entries.push(`Max ${stopAfterUses} usos sesion`);
    }
  }
  if (draft.monitorMode === 'duration') {
    entries.push(`${draft.sessionDurationMinutes || source.duration_minutes || 60} min`);
  }
  if (draft.monitorMode === 'window' && draft.windowStart && draft.windowEnd) {
    entries.push(`${draft.windowStart}-${draft.windowEnd}`);
  }
  entries.push(`Filtros: ${filterTermLabelFromDraft(draft)}`);
  if (source.last_run_at) {
    entries.push(`Ultima ${formatDate(source.last_run_at)}`);
  }
  return entries;
}

function modeLabel(mode: SearchSource['monitor_mode']): string {
  if (mode === 'continuous') {
    return 'Continuo';
  }
  if (mode === 'duration') {
    return 'Duracion';
  }
  if (mode === 'window') {
    return 'Rango horario';
  }
  return 'Puntual';
}
