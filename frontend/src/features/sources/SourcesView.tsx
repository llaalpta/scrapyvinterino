import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ChevronDown, Eraser, ExternalLink, FileText, Pencil, Play, RefreshCw, Save, Square, Trash2, X } from 'lucide-react';
import {
  type MonitorStats,
  type MonitorStatsRange,
  type ProxyTrafficSummary,
  type ProxyProfile,
  type Run,
  type RunEvent,
  type SearchSource,
  type VintedSession,
  type VintedSessionUnusableReason
} from '../../api';
import type { CollectionLoadState } from '../../app/collectionLoadState';
import { formatBytes, formatDate } from '../../utils/format';
import { allActiveProxiesCooling, formatProxyCooldownRemaining, proxyCooldownRemainingMs } from '../../utils/proxyCooldown';
import { eventSearchText } from '../runs/runEventSearch';
import { RunEventEntry } from '../runs/RunsView';
import { buildSourceDraft, filterTermLabelFromSource, sourceDraftHasChanges, type SourceDraft } from './sourceDrafts';

const MonitorPerformanceChart = lazy(() => import('./MonitorPerformanceChart'));

export function SourcesView({
  creatingSource,
  editingSourceId,
  monitorEventHistoryLoadedBySource,
  monitorEventsBySource,
  monitorHiddenEventIdsBySource,
  monitorCommandPending,
  monitorRunsBySource,
  pendingStopSourceIds,
  pendingSourceNavigation,
  monitorStatsBySource,
  monitorStatsRangeBySource,
  onClearMonitorEventsView,
  onBeginSourceEdit,
  onCancelSourceEdit,
  onConfirmDiscardSourceEdit,
  onCreateSource,
  onDeleteSource,
  onLoadMonitorEvents,
  onLoadMonitorRuns,
  onLoadMonitorStats,
  onKeepSourceEditing,
  onRunNow,
  onRetrySession,
  onSelectMonitor,
  onSaveSourceSchedule,
  onStartSession,
  onStopMonitor,
  proxyCollectionState,
  proxyCooldownNowMs,
  proxyProfiles,
  runningSessionId,
  requestedSelectedMonitorId,
  savingSourceId,
  sourceDrafts,
  sourceCollectionState,
  sourceName,
  sources,
  sourceUrl,
  streamStatus,
  streamReady,
  setSourceName,
  setSourceUrl,
  updateSourceDraft,
}: {
  creatingSource: boolean;
  editingSourceId: number | null;
  monitorEventHistoryLoadedBySource: Record<number, boolean>;
  monitorEventsBySource: Record<number, RunEvent[]>;
  monitorHiddenEventIdsBySource: Record<number, number[]>;
  monitorCommandPending: boolean;
  monitorRunsBySource: Record<number, Run[]>;
  pendingStopSourceIds: number[];
  pendingSourceNavigation: { kind: 'monitor'; sourceId: number } | { kind: 'section'; section: string } | null;
  monitorStatsBySource: Record<number, MonitorStats>;
  monitorStatsRangeBySource: Record<number, MonitorStatsRange>;
  onClearMonitorEventsView: (sourceId: number, visibleEventIds: number[]) => void;
  onBeginSourceEdit: (source: SearchSource) => void;
  onCancelSourceEdit: () => void;
  onConfirmDiscardSourceEdit: () => void;
  onCreateSource: (event: FormEvent<HTMLFormElement>) => void;
  onDeleteSource: (source: SearchSource) => void;
  onLoadMonitorEvents: (sourceId: number) => Promise<void>;
  onLoadMonitorRuns: (sourceId: number) => Promise<void>;
  onLoadMonitorStats: (sourceId: number, range: MonitorStatsRange) => void;
  onKeepSourceEditing: () => void;
  onRunNow: (source: SearchSource) => void;
  onRetrySession: (source: SearchSource, proxyProfileId: number) => void;
  onSelectMonitor: (sourceId: number) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  onStopMonitor: (sourceId: number) => void;
  proxyCollectionState: CollectionLoadState;
  proxyCooldownNowMs: number;
  proxyProfiles: ProxyProfile[];
  runningSessionId: number | null;
  requestedSelectedMonitorId: number | null;
  savingSourceId: number | null;
  sourceDrafts: Record<number, SourceDraft>;
  sourceCollectionState: CollectionLoadState;
  sourceName: string;
  sources: SearchSource[];
  sourceUrl: string;
  streamStatus: 'connecting' | 'connected' | 'error';
  streamReady: boolean;
  setSourceName: (value: string) => void;
  setSourceUrl: (value: string) => void;
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
  const coolingProxyProfiles = useMemo(
    () => proxyProfiles.filter(
      (profile) => profile.is_active && proxyCooldownRemainingMs(profile, proxyCooldownNowMs) !== null
    ),
    [proxyCooldownNowMs, proxyProfiles]
  );
  const allKnownActiveProxiesCooling = proxyCollectionState === 'ready'
    && allActiveProxiesCooling(proxyProfiles, proxyCooldownNowMs);
  const defaultSelectedMonitorId = activeSources[0]?.id ?? inactiveSources[0]?.id ?? null;
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
  const sourceCollectionReady = sourceCollectionState === 'ready';

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
          <span>
            {sourceCollectionState === 'loading'
              ? 'Cargando'
              : sourceCollectionState === 'unavailable'
                ? 'No disponible'
                : `${sources.length} configurados`}
          </span>
        </div>
        <form className="source-form" onSubmit={onCreateSource}>
          <input disabled={monitorCommandPending || !sourceCollectionReady} value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="Nombre del monitor" required />
          <input disabled={monitorCommandPending || !sourceCollectionReady} value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="URL de catalogo Vinted" required />
          <button disabled={monitorCommandPending || !sourceCollectionReady} type="submit">{creatingSource ? 'Guardando...' : 'Guardar URL'}</button>
        </form>
      </section>

      <MonitorTable
        drainingSourceIds={drainingSourceIds}
        monitorStatsBySource={monitorStatsBySource}
        selectedMonitorId={selectedMonitorId}
        sourceCollectionState={sourceCollectionState}
        sources={orderedSources}
        onSelectMonitor={onSelectMonitor}
      />

      <section className="monitor-page-card monitor-detail-shell" ref={detailRef} aria-label="Detalle del monitor seleccionado" hidden={!sourceCollectionReady}>
        <MonitorDetail
          editingSourceId={editingSourceId}
          hiddenEventIds={selectedSource ? (monitorHiddenEventIdsBySource[selectedSource.id] ?? []) : []}
          isDraining={selectedSource ? drainingSourceIds.has(selectedSource.id) : false}
          loadingMonitorEvents={selectedSource ? Boolean(loadingMonitorEventsBySource[selectedSource.id]) : false}
          monitorEvents={selectedSource ? (monitorEventsBySource[selectedSource.id] ?? []) : []}
          monitorRuns={selectedSource ? (monitorRunsBySource[selectedSource.id] ?? []) : []}
          monitorRunStateKnown={selectedSource ? Object.hasOwn(monitorRunsBySource, selectedSource.id) : false}
          monitorCommandPending={monitorCommandPending}
          coolingProxyProfiles={coolingProxyProfiles}
          allKnownActiveProxiesCooling={allKnownActiveProxiesCooling}
          onBeginSourceEdit={onBeginSourceEdit}
          onCancelSourceEdit={onCancelSourceEdit}
          onClearMonitorEventsView={onClearMonitorEventsView}
          onDeleteSource={onDeleteSource}
          onLoadMonitorStats={onLoadMonitorStats}
          onRunNow={onRunNow}
          onRetrySession={onRetrySession}
          onSaveSourceSchedule={onSaveSourceSchedule}
          onStartSession={onStartSession}
          onStopMonitor={onStopMonitor}
          runningSessionId={runningSessionId}
          proxyCooldownNowMs={proxyCooldownNowMs}
          savingSourceId={savingSourceId}
          source={selectedSource}
          sourceDrafts={sourceDrafts}
          stats={selectedSource ? (monitorStatsBySource[selectedSource.id] ?? null) : null}
          statsRange={selectedSource ? (monitorStatsRangeBySource[selectedSource.id] ?? 'all') : 'all'}
          streamStatus={streamStatus}
          updateSourceDraft={updateSourceDraft}
        />
      </section>
      {pendingSourceNavigation ? (
        <DiscardSourceEditDialog
          onConfirm={onConfirmDiscardSourceEdit}
          onKeepEditing={onKeepSourceEditing}
        />
      ) : null}
    </section>
  );
}

function MonitorPerformancePanel({
  onRangeChange,
  range,
  source,
  stats
}: {
  onRangeChange: (range: MonitorStatsRange) => void;
  range: MonitorStatsRange;
  source: SearchSource;
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
  const session = stats?.active_session ?? stats?.latest_session ?? null;
  const sessionSummary = stats?.session_summary ?? null;
  const hasBusinessActivityInRange = chartData.some((point) => point.runsCount > 0);
  const hasAnyActivity = stats !== null && (
    (historical?.sessions_count ?? 0) > 0
    || stats.historical_proxy_traffic.state !== 'no_runs'
    || Boolean(stats.active_session ?? stats.latest_session)
  );

  return (
    <section className="monitor-performance">
      <div className="monitor-performance-heading">
        <h4>Rendimiento</h4>
      </div>

      {hasAnyActivity ? (
        <>
          <div className="monitor-performance-table-wrap">
            <table className="monitor-performance-table" aria-label="Comparativa de rendimiento del monitor">
              <colgroup>
                <col className="monitor-performance-scope-column" />
                <col />
                <col />
                <col />
                <col />
                <col />
                <col className="monitor-performance-traffic-column" />
                <col className="monitor-performance-request-column" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Ambito</th>
                  <th scope="col">Tiempo</th>
                  <th scope="col">Ejecuciones</th>
                  <th scope="col">Encontrados</th>
                  <th scope="col">Oportunidades</th>
                  <th scope="col">Fallos</th>
                  <th scope="col">Trafico proxy</th>
                  <th scope="col">Peticiones obs.</th>
                </tr>
              </thead>
              <tbody>
                <MonitorPerformanceRow
                  failedRuns={historical?.failed_runs ?? 0}
                  itemsFound={historical?.items_found ?? 0}
                  label="Acumulado"
                  opportunities={historical?.opportunities_created ?? 0}
                  runsCount={historical?.runs_count ?? 0}
                  secondary={`${historical?.sessions_count ?? 0} sesion${historical?.sessions_count === 1 ? '' : 'es'}`}
                  seconds={historical?.active_seconds ?? 0}
                  traffic={proxyTrafficDisplay(stats?.historical_proxy_traffic ?? null)}
                />
                {session ? (
                  <MonitorPerformanceRow
                    failedRuns={sessionSummary?.failed_runs ?? 0}
                    itemsFound={sessionSummary?.items_found ?? 0}
                    label={stats?.active_session ? 'Sesion activa' : 'Ultima sesion'}
                    opportunities={sessionSummary?.opportunities_created ?? 0}
                    runsCount={sessionSummary?.runs_count ?? 0}
                    secondary={sessionTimingLabel(source, session, Boolean(stats?.active_session))}
                    seconds={session.duration_seconds}
                    traffic={proxyTrafficDisplay(stats?.session_proxy_traffic ?? null)}
                  />
                ) : null}
              </tbody>
            </table>
          </div>
          <p className="monitor-traffic-note">
            Trafico estimado local; DataImpulse mantiene el dato de facturacion autoritativo. La sesion incluye su calibracion inicial.
          </p>

          <section className="monitor-chart-section" aria-label="Historico acumulado por intervalo">
            <div className="monitor-chart-heading">
              <h5>Evolucion acumulada</h5>
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
            {hasBusinessActivityInRange ? (
              <div className="monitor-chart">
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
              </div>
            ) : (
              <p className="monitor-chart-empty">Sin ejecuciones de negocio en este rango.</p>
            )}
          </section>
        </>
      ) : (
        <p className="empty-inline compact">Sin ejecuciones registradas.</p>
      )}
    </section>
  );
}

function MonitorPerformanceRow({
  failedRuns,
  itemsFound,
  label,
  opportunities,
  runsCount,
  secondary,
  seconds,
  traffic
}: {
  failedRuns: number;
  itemsFound: number;
  label: string;
  opportunities: number;
  runsCount: number;
  secondary: string;
  seconds: number;
  traffic: ProxyTrafficDisplay;
}) {
  return (
    <tr>
      <th scope="row">
        <strong>{label}</strong>
        <span>{secondary}</span>
      </th>
      <td data-label="Tiempo">{formatSeconds(seconds)}</td>
      <td data-label="Ejecuciones">{runsCount}</td>
      <td data-label="Encontrados">{itemsFound}</td>
      <td data-label="Oportunidades">{opportunities}</td>
      <td data-label="Fallos">{failedRuns}</td>
      <td data-label="Trafico proxy">{traffic.bytes}</td>
      <td data-label="Peticiones observadas">{traffic.requests}</td>
    </tr>
  );
}

function sessionTimingLabel(source: SearchSource, session: MonitorStats['latest_session'], active: boolean): string {
  if (!session) {
    return '';
  }
  if (active && source.next_run_at) {
    return `Desde ${formatDate(session.started_at)} · proxima ${formatDate(source.next_run_at)}`;
  }
  if (active) {
    return `Desde ${formatDate(session.started_at)}`;
  }
  return session.stopped_at
    ? `${formatDate(session.started_at)} · ${formatDate(session.stopped_at)}`
    : `Desde ${formatDate(session.started_at)}`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

type ProxyTrafficDisplay = {
  bytes: string;
  requests: string;
};

function proxyTrafficDisplay(summary: ProxyTrafficSummary | null): ProxyTrafficDisplay {
  if (!summary || summary.state === 'no_runs') {
    return { bytes: 'Sin ejecuciones', requests: 'Sin ejecuciones' };
  }
  if (summary.state === 'not_applicable') {
    return { bytes: 'No aplica', requests: 'No aplica' };
  }
  if (summary.state === 'not_measured') {
    return { bytes: 'No medido', requests: 'No medido' };
  }
  if (summary.total_observed_bytes === null || summary.observed_requests === null) {
    return { bytes: 'Parcial, sin cifra fiable', requests: 'Parcial, sin cifra fiable' };
  }
  const bytes = formatBytes(summary.total_observed_bytes);
  const requests = String(summary.observed_requests);
  if (summary.state === 'measured') {
    return { bytes, requests };
  }
  const unobserved = summary.unobserved_attempts
    ? ` · ${summary.unobserved_attempts} sin medir`
    : '';
  return {
    bytes: `${bytes} (parcial)`,
    requests: `${requests} obs.${unobserved} (parcial)`
  };
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
  sourceCollectionState,
  sources
}: {
  drainingSourceIds: Set<number>;
  monitorStatsBySource: Record<number, MonitorStats>;
  onSelectMonitor: (sourceId: number) => void;
  selectedMonitorId: number | null;
  sourceCollectionState: CollectionLoadState;
  sources: SearchSource[];
}) {
  return (
    <section className="monitor-page-card monitor-table-panel" aria-label="Monitores configurados">
      <div className="monitor-section-heading compact">
        <div>
          <h3>Monitores configurados</h3>
          <p>Activos primero; selecciona una fila para revisar o editar el monitor.</p>
        </div>
        <span>
          {sourceCollectionState === 'loading'
            ? 'Cargando'
            : sourceCollectionState === 'unavailable'
              ? 'No disponible'
              : sources.length}
        </span>
      </div>
      {sourceCollectionState !== 'ready' ? (
        <p className="empty-inline compact" role="status">
          {sourceCollectionState === 'loading'
            ? 'Cargando monitores...'
            : 'Monitores no disponibles. Vuelve a entrar en Monitores o recarga la PWA para reintentar.'}
        </p>
      ) : sources.length === 0 ? (
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
            const isDraining = drainingSourceIds.has(source.id);
            const summary = monitorSummary(source);
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
  allKnownActiveProxiesCooling,
  coolingProxyProfiles,
  editingSourceId,
  hiddenEventIds,
  isDraining,
  loadingMonitorEvents,
  monitorEvents,
  monitorRuns,
  monitorRunStateKnown,
  monitorCommandPending,
  onBeginSourceEdit,
  onCancelSourceEdit,
  onClearMonitorEventsView,
  onDeleteSource,
  onLoadMonitorStats,
  onRunNow,
  onRetrySession,
  onSaveSourceSchedule,
  onStartSession,
  onStopMonitor,
  proxyCooldownNowMs,
  runningSessionId,
  savingSourceId,
  source,
  sourceDrafts,
  stats,
  statsRange,
  streamStatus,
  updateSourceDraft
}: {
  allKnownActiveProxiesCooling: boolean;
  coolingProxyProfiles: ProxyProfile[];
  editingSourceId: number | null;
  hiddenEventIds: number[];
  isDraining: boolean;
  loadingMonitorEvents: boolean;
  monitorEvents: RunEvent[];
  monitorRuns: Run[];
  monitorRunStateKnown: boolean;
  monitorCommandPending: boolean;
  onBeginSourceEdit: (source: SearchSource) => void;
  onCancelSourceEdit: () => void;
  onClearMonitorEventsView: (sourceId: number, visibleEventIds: number[]) => void;
  onDeleteSource: (source: SearchSource) => void;
  onLoadMonitorStats: (sourceId: number, range: MonitorStatsRange) => void;
  onRunNow: (source: SearchSource) => void;
  onRetrySession: (source: SearchSource, proxyProfileId: number) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onStartSession: (source: SearchSource) => void;
  onStopMonitor: (sourceId: number) => void;
  proxyCooldownNowMs: number;
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
  const [selectedRetryProfileId, setSelectedRetryProfileId] = useState<number | null>(null);
  const archiveDialogRef = useRef<HTMLDivElement | null>(null);
  const eligibleRetryProfiles = useMemo(() => {
    const latestRun = monitorRuns[0];
    const attemptedIds = latestRun?.trigger === 'baseline' && latestRun.status === 'failed'
      ? latestRun.runtime_metadata.session_acquisition_profile_ids
      : null;
    if (!source || source.is_active || !Array.isArray(attemptedIds)) {
      return [];
    }
    const attemptedProfileIds = new Set(
      attemptedIds.filter((value): value is number => typeof value === 'number' && Number.isInteger(value))
    );
    return coolingProxyProfiles.filter((profile) => attemptedProfileIds.has(profile.id));
  }, [coolingProxyProfiles, monitorRuns, source]);
  const effectiveRetryProfileId = eligibleRetryProfiles.some(
    (profile) => profile.id === selectedRetryProfileId
  )
    ? selectedRetryProfileId
    : (eligibleRetryProfiles[0]?.id ?? null);

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
  const isEditing = editingSourceId === source.id && !source.is_active && !isDraining;

  return (
    <div className={`monitor-detail-content${source.is_active || isDraining ? ' active-monitor-detail' : ' inactive-monitor-detail'}`}>
      <header className="monitor-detail-header">
        <div className="monitor-detail-heading-copy">
          <div className="monitor-detail-title-line">
            <h3>{source.name}</h3>
            <span className={source.is_active || isDraining ? 'status running' : 'status'}>
              {isDraining ? 'Deteniendo...' : source.is_active ? 'Activo' : 'Inactivo'}
            </span>
          </div>
          {isEditing ? <p>Modifica la configuracion persistida de este monitor.</p> : null}
        </div>
        {!isEditing ? (
          <a className="monitor-catalog-link" href={source.url} target="_blank" rel="noreferrer" title={source.url}>
            <ExternalLink size={16} />
            Abrir catalogo
          </a>
        ) : null}
      </header>

      {coolingProxyProfiles.length > 0 ? (
        <div className="empty-inline compact">
          <strong>Cooldown de proxy activo.</strong>{' '}
          {coolingProxyProfiles.map((profile) => {
            const cooldownUntil = profile.cooldown_until;
            const remainingMs = proxyCooldownRemainingMs(profile, proxyCooldownNowMs);
            return remainingMs === null || !cooldownUntil
              ? null
              : `${profile.name}: ${profile.failure_count} fallos, hasta ${formatDate(cooldownUntil)}, restan ${formatProxyCooldownRemaining(remainingMs)}`;
          }).filter(Boolean).join(' | ')}
        </div>
      ) : null}

      <div className="monitor-config-actions monitor-primary-actions" aria-label="Acciones del monitor seleccionado">
        {isEditing ? (
          <>
            <button
              type="button"
              disabled={savingSourceId === source.id || hasCommandInFlight || !hasUnsavedChanges}
              title="Guardar configuracion"
              onClick={() => onSaveSourceSchedule(source)}
            >
              <Save size={16} />
              {savingSourceId === source.id ? 'Guardando...' : 'Guardar'}
            </button>
            <button type="button" disabled={monitorCommandPending} onClick={onCancelSourceEdit}>
              <X size={16} />
              Cancelar
            </button>
            {hasUnsavedChanges ? <span className="monitor-config-dirty">Cambios sin guardar</span> : null}
          </>
        ) : source.is_active ? (
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
        ) : isDraining ? (
          <button type="button" disabled>
            <Square size={16} />
            Deteniendo sesion...
          </button>
        ) : (
          <>
            {eligibleRetryProfiles.length > 0 ? (
              <>
                {eligibleRetryProfiles.length > 1 ? (
                  <label className="source-schedule">
                    <span className="sr-only">Perfil proxy para el reintento</span>
                    <select
                      aria-label="Perfil proxy para el reintento"
                      disabled={hasCommandInFlight}
                      value={effectiveRetryProfileId ?? ''}
                      onChange={(event) => setSelectedRetryProfileId(Number(event.target.value))}
                    >
                      {eligibleRetryProfiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>{profile.name}</option>
                      ))}
                    </select>
                  </label>
                ) : null}
                <button
                  type="button"
                  disabled={
                    hasCommandInFlight
                    || savingSourceId === source.id
                    || launchBlockedByFilters
                    || effectiveRetryProfileId === null
                  }
                  title="Omitir una vez el cooldown del perfil seleccionado y probar un sticky nuevo"
                  onClick={() => {
                    if (effectiveRetryProfileId !== null) {
                      onRetrySession(source, effectiveRetryProfileId);
                    }
                  }}
                >
                  <RefreshCw size={17} />
                  {runningSessionId === source.id
                    ? 'Reintentando...'
                    : eligibleRetryProfiles.length === 1
                      ? `Reintentar con ${eligibleRetryProfiles[0].name}`
                      : 'Reintentar sesion'}
                </button>
              </>
            ) : null}
            <button
              type="button"
              disabled={hasCommandInFlight || savingSourceId === source.id || launchBlockedByFilters || allKnownActiveProxiesCooling}
              title={
                launchBlockedByFilters
                  ? 'Corrige los filtros de URL no soportados antes de ejecutar este monitor'
                  : allKnownActiveProxiesCooling
                    ? 'Todos los proxys activos estan en cooldown; espera a la expiracion indicada'
                    : isManual
                      ? 'Iniciar sesion y calibrar el listado actual'
                      : 'Iniciar sesion periodica y calibrar el listado actual'
              }
              onClick={() => onStartSession(source)}
            >
              <Play size={17} />
              {runningSessionId === source.id
                ? 'Iniciando...'
                : 'Iniciar sesion'}
            </button>
            <button type="button" disabled={hasCommandInFlight} onClick={() => onBeginSourceEdit(source)}>
              <Pencil size={16} />
              Modificar
            </button>
            <button
              className="danger-button monitor-archive-action"
              type="button"
              disabled={savingSourceId === source.id || hasCommandInFlight}
              title="Archivar monitor"
              onClick={() => setArchiveSource(source)}
            >
              <Trash2 size={16} />
              Archivar monitor
            </button>
          </>
        )}
      </div>

      {isEditing ? (
        <section className="monitor-config-panel editing">
          <div className="monitor-config-heading">
            <h4>Modificar configuracion</h4>
            <span>La URL y sus filtros efectivos se validan al guardar.</span>
          </div>
          <MonitorConfigEditor
            disabled={hasCommandInFlight}
            source={source}
            sourceDraft={sourceDraft}
            updateSourceDraft={updateSourceDraft}
          />
        </section>
      ) : (
        <CatalogFilterCompatibilityStatus source={source} />
      )}

      <PreparedSessionsPanel key={`contexts-${source.id}`} sessions={source.prepared_sessions} />

      <MonitorPerformancePanel
        range={statsRange}
        source={source}
        stats={stats}
        onRangeChange={(range) => onLoadMonitorStats(source.id, range)}
      />

      <details className="monitor-logs" key={`logs-${source.id}`}>
        <summary className="monitor-logs-header">
          <div className="monitor-logs-title">
            <FileText size={15} />
            <h4>Logs acumulados</h4>
            <span>{historicalRunCountLabel}</span>
          </div>
          <span className="monitor-logs-toggle" aria-hidden="true">
            <span className="monitor-logs-toggle-closed">Mostrar</span>
            <span className="monitor-logs-toggle-open">Ocultar</span>
            <ChevronDown size={17} />
          </span>
        </summary>
        <div className="monitor-logs-body">
          <div className="monitor-logs-actions">
            <p className="monitor-log-note">Solo oculta eventos en esta pantalla; el historico permanece guardado.</p>
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
          <MonitorEventTimeline
            key={source.id}
            events={visibleMonitorEvents}
            loading={loadingMonitorEvents}
            streamStatus={source.is_active || isDraining ? streamStatus : null}
            viewCleared={logViewCleared}
          />
        </div>
      </details>

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

function DiscardSourceEditDialog({
  onConfirm,
  onKeepEditing
}: {
  onConfirm: () => void;
  onKeepEditing: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  return (
    <div className="confirm-dialog-backdrop" role="presentation" onClick={onKeepEditing}>
      <div
        aria-describedby="discard-monitor-edit-description"
        aria-labelledby="discard-monitor-edit-title"
        aria-modal="true"
        className="confirm-dialog"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            onKeepEditing();
          }
        }}
      >
        <h4 id="discard-monitor-edit-title">Cambios sin guardar</h4>
        <p id="discard-monitor-edit-description">
          La configuracion modificada no se ha guardado. Puedes seguir editando o descartarla para continuar.
        </p>
        <div className="confirm-dialog-actions">
          <button type="button" onClick={onKeepEditing}>Seguir editando</button>
          <button className="danger-button" type="button" onClick={onConfirm}>Descartar y continuar</button>
        </div>
      </div>
    </div>
  );
}

function PreparedSessionsPanel({ sessions }: { sessions: VintedSession[] }) {
  const usableCount = sessions.filter((session) => session.usable_now).length;
  const contextLabel = sessions.length === 1 ? ` · ${preparedSessionSummary(sessions[0])}` : '';
  const availabilityLabel = sessions.length === 1
    ? sessions[0].usable_now
      ? '1 contexto listo'
      : '1 contexto no reutilizable'
    : `${usableCount} de ${sessions.length} contextos listos`;
  const summaryLabel = sessions.length === 0
    ? 'Sin contexto'
    : `${availabilityLabel}${contextLabel}`;

  return (
    <details
      aria-label="Contextos HTTP preparados para este monitor"
      className={`monitor-logs monitor-http-contexts${sessions.length > 0 && usableCount === 0 ? ' warning' : ''}`}
    >
      <summary className="monitor-logs-header">
        <div className="monitor-logs-title">
          <RefreshCw size={15} />
          <h4>Contextos HTTP preparados</h4>
          <span>{summaryLabel}</span>
        </div>
        <span className="monitor-logs-toggle" aria-hidden="true">
          <span className="monitor-logs-toggle-closed">Mostrar</span>
          <span className="monitor-logs-toggle-open">Ocultar</span>
          <ChevronDown size={17} />
        </span>
      </summary>
      <div className="monitor-logs-body monitor-http-contexts-body">
        <p className="monitor-log-note">
          Cada uso es una preparacion o seleccion del contexto para un run, no una peticion HTTP. Se comprueba al iniciar el run: si ha
          caducado o se ha agotado, se prepara otro; un run ya iniciado no se interrumpe y un fallo de preparacion queda visible.
        </p>
        {sessions.length === 0 ? (
          <p className="empty-inline compact">Sin contexto HTTP preparado para este monitor.</p>
        ) : (
          <div className="monitor-prepared-session-list">
            {sessions.map((session) => (
              <PreparedSessionRow key={session.proxy_profile_id} session={session} />
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

function PreparedSessionRow({ session }: { session: VintedSession }) {
  const reason = session.unusable_reason ? preparedSessionReasonLabel(session.unusable_reason) : null;

  return (
    <article className={`catalog-filter-status monitor-prepared-session-row ${session.usable_now ? 'ready' : 'blocked'}`}>
      <div className="monitor-prepared-session-main">
        <div>
          <strong>{session.proxy_name}</strong>
          <span>Contexto #{session.id} | estado durable {session.status}</span>
        </div>
        <span className={session.usable_now ? 'status active' : 'status failed'}>
          {session.usable_now ? 'Reutilizable' : 'No reutilizable'}
        </span>
      </div>
      <p>{session.usable_now ? 'Se reutilizara mientras no caduque ni alcance su limite.' : reason ?? 'Motivo no disponible.'}</p>
      <dl className="monitor-session-strip">
        <Metric label="Usos del contexto" value={`${session.request_count}/${session.max_requests}`} />
        <Metric label="Caduca" value={session.expires_at ? formatDate(session.expires_at) : 'Sin caducidad'} />
        <Metric label="Ultimo uso" value={session.last_used_at ? formatDate(session.last_used_at) : 'Nunca'} />
      </dl>
    </article>
  );
}

function preparedSessionSummary(session: VintedSession): string {
  const usage = `${session.request_count}/${session.max_requests} usos`;
  if (session.unusable_reason === 'exhausted') {
    return `${usage} · agotado`;
  }
  if (!session.expires_at) {
    return `${usage} · sin caducidad`;
  }
  const expiry = formatDate(session.expires_at);
  return session.unusable_reason === 'expired'
    ? `${usage} · caduco ${expiry}`
    : `${usage} · caduca ${expiry}`;
}

function preparedSessionReasonLabel(reason: VintedSessionUnusableReason): string {
  const labels: Record<VintedSessionUnusableReason, string> = {
    status_incomplete: 'La preparacion quedo incompleta.',
    status_invalid: 'El contexto fue invalidado.',
    status_unrecognized: 'El estado durable no es reconocido.',
    proxy_identity_mismatch: 'La identidad efectiva del proxy ha cambiado.',
    browser_profile_mismatch: 'El perfil de navegador ya no coincide.',
    request_context_mismatch: 'El contexto HTTP efectivo ya no coincide.',
    expired: 'El contexto ha expirado.',
    exhausted: 'El contexto ha agotado su limite de usos.',
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
  const ignored = Object.entries(compatibility.ignored).filter(([key]) => key !== 'order' && key !== 'page');
  const unsupported = Object.entries(compatibility.unsupported);
  const applicationControlled = ['order', 'page']
    .map((key) => [key, compatibility.api_params[key]] as const)
    .filter((entry): entry is readonly [string, string | number] => typeof entry[1] === 'string' || typeof entry[1] === 'number');
  const summaryLabel = [
    `${supported.length} filtro${supported.length === 1 ? '' : 's'} URL`,
    `${applicationControlled.length} controlado${applicationControlled.length === 1 ? '' : 's'}`,
    `${ignored.length} sin efecto`
  ].join(' · ');
  const details = (
    <div className="catalog-filter-details">
      {supported.length > 0 ? (
        <span>Aplicados desde la URL: {formatFilterEntries(supported)}</span>
      ) : (
        <span>Sin filtros de producto aplicados desde la URL.</span>
      )}
      {applicationControlled.length > 0 ? (
        <span>Controlados por la aplicacion: {formatCatalogApiParams(applicationControlled)}</span>
      ) : null}
      {ignored.length > 0 ? (
        <details className="catalog-filter-ignored">
          <summary>{ignored.length} parametros sin efecto desde la URL</summary>
          <span>{formatFilterEntries(ignored)}</span>
        </details>
      ) : null}
      {unsupported.length > 0 ? <strong>Bloquean: {formatFilterEntries(unsupported)}</strong> : null}
    </div>
  );
  if (compatibility.compatible) {
    return (
      <details className="catalog-filter-status catalog-filter-summary ready" key={`filters-${source.id}`}>
        <summary>
          <span className="catalog-filter-status-title">Filtros URL compatibles</span>
          <span>{summaryLabel}</span>
          <ChevronDown aria-hidden="true" size={17} />
        </summary>
        {details}
      </details>
    );
  }
  return (
    <div className="catalog-filter-status blocked">
      <div className="catalog-filter-status-title">
        <span>Filtros URL no soportados</span>
      </div>
      {details}
    </div>
  );
}

function formatCatalogApiParams(entries: ReadonlyArray<readonly [string, string | number]>): string {
  return entries.map(([key, value]) => `${key}=${value}`).join(' · ');
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
            <label className="monitor-context-limit-field">
              Detener tras N runs con el mismo contexto
              <input
                type="number"
                min="1"
                max="1000"
                disabled={disabled}
                placeholder="sin limite"
                value={sourceDraft.stopAfterVintedSessionUses}
                onChange={(event) => updateSourceDraft(source.id, 'stopAfterVintedSessionUses', event.target.value)}
              />
              <small>Vacio: continua. Al rotar el contexto, el contador vuelve a empezar.</small>
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
      entries.push(`Max ${config.stop_after_vinted_session_uses} runs/contexto`);
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
