import { Component, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Eraser, FileText, Play, RefreshCw, Save, Square, Trash2 } from 'lucide-react';
import { monitorEventsStreamUrl, type MonitorStats, type MonitorStatsRange, type Run, type RunEvent, type SearchSource } from '../../api';
import { formatDate } from '../../utils/format';
import { eventSearchText, RunEventEntry } from '../runs/RunsView';
import { buildSourceDraft, filterTermLabelFromDraft, filterTermLabelFromSource, sourceDraftHasChanges, type SourceDraft } from './sourceDrafts';

const MonitorPerformanceChart = lazy(() => import('./MonitorPerformanceChart'));

export function SourcesView({
  monitorEventsBySource,
  monitorHiddenEventIdsBySource,
  monitorRunsBySource,
  monitorStatsBySource,
  monitorStatsRangeBySource,
  onAppendMonitorEvent,
  onClearMonitorEventsView,
  onCreateSource,
  onDeleteSource,
  onLoadMonitorEvents,
  onLoadMonitorRuns,
  onLoadMonitorStats,
  onRefreshRuntime,
  onRecalibrateBaseline,
  onSaveSourceSchedule,
  onStartSession,
  onStopMonitor,
  runningSessionId,
  savingSourceId,
  sourceDrafts,
  sourceName,
  sources,
  sourceUrl,
  setSourceName,
  setSourceUrl,
  updateSourceDraft,
}: {
  monitorEventsBySource: Record<number, RunEvent[]>;
  monitorHiddenEventIdsBySource: Record<number, number[]>;
  monitorRunsBySource: Record<number, Run[]>;
  monitorStatsBySource: Record<number, MonitorStats>;
  monitorStatsRangeBySource: Record<number, MonitorStatsRange>;
  onAppendMonitorEvent: (event: RunEvent) => void;
  onClearMonitorEventsView: (sourceId: number, visibleEventIds: number[]) => void;
  onCreateSource: (event: FormEvent<HTMLFormElement>) => void;
  onDeleteSource: (source: SearchSource) => void;
  onLoadMonitorEvents: (sourceId: number) => Promise<void>;
  onLoadMonitorRuns: (sourceId: number) => void;
  onLoadMonitorStats: (sourceId: number, range: MonitorStatsRange) => void;
  onRefreshRuntime: () => Promise<void>;
  onRecalibrateBaseline: (source: SearchSource) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  onStopMonitor: (sourceId: number) => void;
  runningSessionId: number | null;
  savingSourceId: number | null;
  sourceDrafts: Record<number, SourceDraft>;
  sourceName: string;
  sources: SearchSource[];
  sourceUrl: string;
  setSourceName: (value: string) => void;
  setSourceUrl: (value: string) => void;
  updateSourceDraft: (sourceId: number, field: keyof SourceDraft, value: string) => void;
}) {
  const activeSources = useMemo(() => sources.filter((source) => source.is_active), [sources]);
  const inactiveSources = useMemo(() => sources.filter((source) => !source.is_active), [sources]);
  const orderedSources = useMemo(() => [...activeSources, ...inactiveSources], [activeSources, inactiveSources]);
  const activeSourceIds = useMemo(() => new Set(activeSources.map((source) => source.id)), [activeSources]);
  const activeSourceKey = useMemo(() => activeSources.map((source) => source.id).join(','), [activeSources]);
  const defaultSelectedMonitorId = activeSources[0]?.id ?? inactiveSources[0]?.id ?? null;
  const [requestedSelectedMonitorId, setSelectedMonitorId] = useState<number | null>(null);
  const selectedMonitorId = sources.some((source) => source.id === requestedSelectedMonitorId)
    ? requestedSelectedMonitorId
    : defaultSelectedMonitorId;
  const selectedSource = useMemo(
    () => sources.find((source) => source.id === selectedMonitorId) ?? null,
    [selectedMonitorId, sources]
  );
  const refreshTimerRef = useRef<number | null>(null);
  const loadingStatsRef = useRef<Set<string>>(new Set());
  const loadingRunsRef = useRef<Set<number>>(new Set());
  const loadingEventsRef = useRef<Set<number>>(new Set());
  const detailRef = useRef<HTMLElement | null>(null);
  const [loadingMonitorEventsBySource, setLoadingMonitorEventsBySource] = useState<Record<number, boolean>>({});
  const [streamState, setStreamState] = useState<{ sourceKey: string; status: 'connecting' | 'connected' | 'error' }>({
    sourceKey: '',
    status: 'connecting'
  });
  const streamStatus = activeSourceKey === '' || streamState.sourceKey !== activeSourceKey ? 'connecting' : streamState.status;
  const handleRunEvent = useCallback(
    (event: RunEvent) => {
      onAppendMonitorEvent(event);
      if (!event.source_id || !activeSourceIds.has(event.source_id) || !shouldRefreshRuns(event.phase)) {
        return;
      }
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
      refreshTimerRef.current = window.setTimeout(() => {
        refreshTimerRef.current = null;
        void onRefreshRuntime();
      }, 400);
    },
    [activeSourceIds, onAppendMonitorEvent, onRefreshRuntime]
  );

  useEffect(() => {
    if (activeSourceKey === '') {
      return undefined;
    }
    const events = new EventSource(monitorEventsStreamUrl());
    events.addEventListener('open', () => setStreamState({ sourceKey: activeSourceKey, status: 'connected' }));
    events.addEventListener('error', () => setStreamState({ sourceKey: activeSourceKey, status: 'error' }));
    events.addEventListener('monitor_event', (message) => {
      const event = parseRunEvent(message);
      if (event) {
        handleRunEvent(event);
      }
    });
    return () => events.close();
  }, [activeSourceKey, handleRunEvent]);

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
    };
  }, []);

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
    onLoadMonitorRuns(selectedSource.id);
  }, [monitorRunsBySource, onLoadMonitorRuns, selectedSource]);

  useEffect(() => {
    if (!selectedSource) {
      return;
    }
    if (monitorEventsBySource[selectedSource.id]) {
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
  }, [monitorEventsBySource, onLoadMonitorEvents, selectedSource]);

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
          <input value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="Nombre del monitor" required />
          <input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="URL de catalogo Vinted" required />
          <button type="submit">Guardar URL</button>
        </form>
      </section>

      <MonitorTable
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
          {selectedSource ? <span>{selectedSource.is_active ? 'Activo' : 'Inactivo'}</span> : <span>Sin seleccion</span>}
        </div>
        <MonitorDetail
          hiddenEventIds={selectedSource ? (monitorHiddenEventIdsBySource[selectedSource.id] ?? []) : []}
          loadingMonitorEvents={selectedSource ? Boolean(loadingMonitorEventsBySource[selectedSource.id]) : false}
          monitorEvents={selectedSource ? (monitorEventsBySource[selectedSource.id] ?? []) : []}
          monitorRuns={selectedSource ? (monitorRunsBySource[selectedSource.id] ?? []) : []}
          onClearMonitorEventsView={onClearMonitorEventsView}
          onDeleteSource={onDeleteSource}
          onLoadMonitorStats={onLoadMonitorStats}
          onRecalibrateBaseline={onRecalibrateBaseline}
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

  return (
    <div className="run-events monitor-event-timeline">
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
    return 'Stream no disponible; historico cargado';
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

function parseRunEvent(message: MessageEvent): RunEvent | null {
  try {
    return JSON.parse(message.data) as RunEvent;
  } catch {
    return null;
  }
}

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
  monitorStatsBySource,
  onSelectMonitor,
  selectedMonitorId,
  sources,
  sourceDrafts
}: {
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
            const summary = source.is_active ? monitorSummary(source) : draftSummary(source, draft);
            return (
              <MonitorTableRow
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
  isSelected,
  onSelect,
  source,
  stats,
  summary
}: {
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
    source.is_active ? 'is-active' : '',
    isSelected ? 'selected' : ''
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button
      className={rowClassName}
      type="button"
      aria-pressed={isSelected}
      aria-label={`${source.name}, ${source.is_active ? 'activo' : 'inactivo'}`}
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
          <span className={source.is_active ? 'status running' : 'status'}>{source.is_active ? 'Activo' : 'Inactivo'}</span>
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
  hiddenEventIds,
  loadingMonitorEvents,
  monitorEvents,
  monitorRuns,
  onClearMonitorEventsView,
  onDeleteSource,
  onLoadMonitorStats,
  onRecalibrateBaseline,
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
  updateSourceDraft
}: {
  hiddenEventIds: number[];
  loadingMonitorEvents: boolean;
  monitorEvents: RunEvent[];
  monitorRuns: Run[];
  onClearMonitorEventsView: (sourceId: number, visibleEventIds: number[]) => void;
  onDeleteSource: (source: SearchSource) => void;
  onLoadMonitorStats: (sourceId: number, range: MonitorStatsRange) => void;
  onRecalibrateBaseline: (source: SearchSource) => void;
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
  const launchBlockedByBaseline = !source.baseline_ready;
  const launchBlockedByFilters = source.catalog_filter_compatibility ? !source.catalog_filter_compatibility.compatible : false;

  return (
    <div className={`monitor-detail-content${source.is_active ? ' active-monitor-detail' : ' inactive-monitor-detail'}`}>
      <div className="source-card-header">
        <div className="source-main">
          <strong>{source.name}</strong>
          <a href={source.url} target="_blank" rel="noreferrer">
            {source.url}
          </a>
        </div>
        <div className="source-badges">
          <span className={source.is_active ? 'status running' : 'status'}>{source.is_active ? 'Activo' : 'Inactivo'}</span>
        </div>
      </div>

      <MonitorSessionOverview source={source} stats={stats} />

      <section className={`monitor-config-panel${source.is_active ? ' readonly' : ''}`}>
        <div className="monitor-config-heading">
          <h4>Configuracion</h4>
          {source.is_active ? <span>Deten el monitor para editarla.</span> : <span>Editable con el monitor detenido.</span>}
        </div>
        <div className={`baseline-status ${source.baseline_ready ? 'ready' : 'pending'}`}>
          <span>{source.baseline_ready ? 'Snapshot inicial calibrado' : 'Snapshot inicial pendiente'}</span>
          {source.baseline_policy_hash ? <code>{source.baseline_policy_hash}</code> : null}
        </div>
        <CatalogFilterCompatibilityStatus source={source} />
        <MonitorConfigEditor
          disabled={source.is_active}
          source={source}
          sourceDraft={sourceDraft}
          updateSourceDraft={updateSourceDraft}
        />
        <div className="monitor-config-actions">
          {source.is_active ? (
            <button type="button" disabled={savingSourceId === source.id} onClick={() => onStopMonitor(source.id)}>
              <Square size={16} />
              Detener sesion
            </button>
          ) : (
            <>
              <button type="button" disabled={savingSourceId === source.id || !hasUnsavedChanges} title="Guardar monitor" onClick={() => onSaveSourceSchedule(source)}>
                <Save size={16} />
                Guardar
              </button>
              <button
                type="button"
                disabled={savingSourceId === source.id || runningSessionId !== null || hasUnsavedChanges || launchBlockedByFilters}
                title={
                  hasUnsavedChanges
                    ? 'Guarda los cambios antes de recalibrar'
                    : launchBlockedByFilters
                      ? 'Corrige los filtros de URL no soportados antes de recalibrar'
                      : 'Recalibrar listado inicial'
                }
                onClick={() => onRecalibrateBaseline(source)}
              >
                <RefreshCw size={16} />
                Recalibrar listado inicial
              </button>
              <button
                type="button"
                disabled={runningSessionId !== null || savingSourceId === source.id || hasUnsavedChanges || launchBlockedByBaseline || launchBlockedByFilters}
                title={
                  hasUnsavedChanges
                    ? 'Guarda los cambios antes de lanzar la sesion'
                    : launchBlockedByFilters
                      ? 'Corrige los filtros de URL no soportados antes de ejecutar este monitor'
                    : launchBlockedByBaseline
                      ? 'Recalibra el listado inicial antes de ejecutar este monitor'
                      : 'Lanzar sesion'
                }
                onClick={() => onStartSession(source)}
              >
                <Play size={17} />
                Lanzar sesion
              </button>
              <button
                className="danger-button"
                type="button"
                disabled={savingSourceId === source.id}
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
          events={visibleMonitorEvents}
          loading={loadingMonitorEvents}
          streamStatus={source.is_active ? streamStatus : null}
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
                disabled={savingSourceId === archiveSource.id}
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
          Terminos excluyentes
          <textarea
            disabled={disabled}
            value={sourceDraft.filterTerms}
            rows={4}
            placeholder="manchas, roto, destenido"
            onChange={(event) => updateSourceDraft(source.id, 'filterTerms', event.target.value)}
          />
        </label>
        <span>Solo se aplican a este monitor.</span>
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

function shouldRefreshRuns(phase: string): boolean {
  return phase === 'run_started' || phase === 'run_succeeded' || phase === 'run_failed';
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
