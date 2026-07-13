import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  announceAuthenticationRequired,
  calibrateMonitorBaseline,
  createProxyProfile,
  createSource,
  deleteSource,
  fetchMonitorEvents,
  fetchMonitorStats,
  fetchOpportunities,
  fetchProxyProfiles,
  fetchRunEvents,
  fetchRuns,
  fetchScheduler,
  fetchSources,
  monitorEventsStreamUrl,
  prepareMonitorVintedSession,
  probeMonitorItemDetail,
  revalidateLocalAuthentication,
  runMonitor,
  startMonitor,
  stopMonitor,
  testProxyProfile,
  updateScheduler,
  updateProxyProfile,
  updateSource,
  type MonitorStats,
  type MonitorStatsRange,
  type OpportunityResult,
  type Page,
  type ProxyProfile,
  type SchedulerUpdate,
  type Run,
  type RunEvent,
  type SchedulerState,
  type SearchSource
} from '../api';
import { navItems } from '../app/navigation';
import {
  buildOpportunityQuery,
  defaultOpportunityFilters,
  type OpportunityFilters
} from '../features/opportunities/opportunityFilters';
import { type ProxyDraft } from '../features/settings/SettingsView';
import { buildSourceDraft, buildSourceDrafts, parseFilterTerms, sourceDraftHasChanges, type SourceDraft } from '../features/sources/sourceDrafts';

const emptyOpportunityPage: Page<OpportunityResult> = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0 };
const emptyProxyDraft: ProxyDraft = {
  name: '',
  scheme: 'http',
  kind: 'own',
  host: '',
  port: '',
  maxConcurrentRuns: '1',
  username: '',
  password: '',
  countryCode: 'ES'
};
const DEFAULT_MONITOR_STATS_RANGE: MonitorStatsRange = 'all';
const MONITOR_RUN_HISTORY_LIMIT = 1000;
const MONITOR_STREAM_LIVENESS_TIMEOUT_MS = 22_500;
const MONITOR_STREAM_AUTH_TIMEOUT_MS = 10_000;

export function useDashboardController() {
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [proxyProfiles, setProxyProfiles] = useState<ProxyProfile[]>([]);
  const [opportunityPage, setOpportunityPage] = useState<Page<OpportunityResult>>(emptyOpportunityPage);
  const [runs, setRuns] = useState<Run[]>([]);
  const [monitorRunsBySource, setMonitorRunsBySource] = useState<Record<number, Run[]>>({});
  const [monitorEventsBySource, setMonitorEventsBySource] = useState<Record<number, RunEvent[]>>({});
  const [monitorEventHistoryLoadedBySource, setMonitorEventHistoryLoadedBySource] = useState<Record<number, boolean>>({});
  const [monitorHiddenEventIdsBySource, setMonitorHiddenEventIdsBySource] = useState<Record<number, number[]>>({});
  const [monitorStatsBySource, setMonitorStatsBySource] = useState<Record<number, MonitorStats>>({});
  const [monitorStatsRangeBySource, setMonitorStatsRangeBySource] = useState<Record<number, MonitorStatsRange>>({});
  const [detailProbeRefs, setDetailProbeRefs] = useState<Record<number, string>>({});
  const [detailProbeMessages, setDetailProbeMessages] = useState<Record<number, string>>({});
  const [scheduler, setScheduler] = useState<SchedulerState | null>(null);
  const [schedulerAvailabilityError, setSchedulerAvailabilityError] = useState<string | null>(null);
  const [sourceDrafts, setSourceDrafts] = useState<Record<number, SourceDraft>>({});
  const [opportunityFilters, setOpportunityFilters] = useState<OpportunityFilters>(defaultOpportunityFilters);
  const [opportunitiesPageSize, setOpportunitiesPageSize] = useState(25);
  const [runningSessionId, setRunningSessionId] = useState<number | null>(null);
  const [savingSourceId, setSavingSourceId] = useState<number | null>(null);
  const [savingScheduler, setSavingScheduler] = useState(false);
  const [savingProxy, setSavingProxy] = useState(false);
  const [testingProxyIds, setTestingProxyIds] = useState<number[]>([]);
  const [proxyActionMessages, setProxyActionMessages] = useState<Record<number, string>>({});
  const [loadingOpportunities, setLoadingOpportunities] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [proxyDraft, setProxyDraft] = useState<ProxyDraft>(emptyProxyDraft);
  const [activeSection, setActiveSection] = useState('opportunities');
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [monitorStreamStatus, setMonitorStreamStatus] = useState<'connecting' | 'connected' | 'error'>('connecting');
  const [monitorStreamReady, setMonitorStreamReady] = useState(false);
  const activeTitle = useMemo(() => navItems.find((item) => item.id === activeSection)?.label ?? 'Oportunidades', [activeSection]);
  const activeSubtitle = useMemo(
    () => sectionSubtitle(activeSection, opportunityPage.total, sources.length),
    [activeSection, opportunityPage.total, sources.length]
  );
  const monitorStreamCursorRef = useRef<number | null>(null);
  const monitorStreamSeenEventIdsRef = useRef<Set<number>>(new Set());
  const pendingTerminalEventsRef = useRef<Map<number, boolean>>(new Map());
  const terminalRefreshTimerRef = useRef<number | null>(null);
  const monitorStreamReconnectTimerRef = useRef<number | null>(null);
  const monitorRunsRequestGenerationRef = useRef<Map<number, number>>(new Map());
  const monitorStatsRequestGenerationRef = useRef<Map<number, number>>(new Map());
  const monitorStreamRuntimeRef = useRef({
    sourceIds: new Set<number>(),
    statsRanges: monitorStatsRangeBySource,
    opportunityFilters,
    opportunitiesPageSize
  });
  monitorStreamRuntimeRef.current = {
    sourceIds: new Set(sources.map((source) => source.id)),
    statsRanges: monitorStatsRangeBySource,
    opportunityFilters,
    opportunitiesPageSize
  };

  const refreshLoadedMonitorStats = useCallback(
    async (sourceData: SearchSource[]) => {
      if (sourceData.length === 0) {
        setMonitorStatsBySource({});
        return;
      }
      const visibleIds = new Set(sourceData.map((source) => source.id));
      const loadedIds = Object.keys(monitorStatsBySource)
        .map(Number)
        .filter((sourceId) => visibleIds.has(sourceId));
      if (loadedIds.length === 0) {
        setMonitorStatsBySource({});
        return;
      }
      const entries = await Promise.all(
        loadedIds.map(async (sourceId) => {
          const range = monitorStatsRangeBySource[sourceId] ?? DEFAULT_MONITOR_STATS_RANGE;
          return [sourceId, await fetchMonitorStats(sourceId, range)] as const;
        })
      );
      setMonitorStatsBySource(Object.fromEntries(entries));
    },
    [monitorStatsBySource, monitorStatsRangeBySource]
  );

  useEffect(() => {
    let disposed = false;
    Promise.all([
      fetchSources(),
      fetchOpportunities(),
      fetchRuns(),
      fetchProxyProfiles()
    ])
      .then(([sourceData, opportunityData, runData, proxyData]) => {
        if (disposed) {
          return;
        }
        setSources(sourceData);
        setOpportunityPage(opportunityData);
        setRuns(runData);
        setProxyProfiles(proxyData);
        setSourceDrafts(buildSourceDrafts(sourceData));
      })
      .catch((caught: unknown) => {
        if (!disposed) {
          setError(caught instanceof Error ? caught.message : 'Error cargando datos');
        }
      });

    void fetchScheduler()
      .then((schedulerData) => {
        if (!disposed) {
          setScheduler(schedulerData);
          setSchedulerAvailabilityError(null);
        }
      })
      .catch(() => {
        if (!disposed) {
          setScheduler(null);
          setSchedulerAvailabilityError('No se pudo consultar la disponibilidad del scheduler.');
        }
      });

    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    if (activeSection !== 'settings') {
      return undefined;
    }

    let disposed = false;
    const refreshSchedulerAvailability = async () => {
      try {
        const schedulerData = await fetchScheduler();
        if (!disposed) {
          setScheduler(schedulerData);
          setSchedulerAvailabilityError(null);
        }
      } catch {
        if (!disposed) {
          setScheduler(null);
          setSchedulerAvailabilityError('No se pudo consultar la disponibilidad del scheduler.');
        }
      }
    };

    void refreshSchedulerAvailability();
    const interval = window.setInterval(() => void refreshSchedulerAvailability(), 5000);
    return () => {
      disposed = true;
      window.clearInterval(interval);
    };
  }, [activeSection]);

  useEffect(() => {
    if (activeSection !== 'sources') {
      return undefined;
    }

    let disposed = false;
    const pendingTerminalEvents = pendingTerminalEventsRef.current;
    let stream: EventSource | null = null;
    let streamLivenessTimer: number | null = null;
    let authRevalidationController: AbortController | null = null;
    let authRevalidationTimer: number | null = null;

    const refreshTerminalBatch = async () => {
      const pending = new Map(pendingTerminalEvents);
      pendingTerminalEvents.clear();
      terminalRefreshTimerRef.current = null;
      if (pending.size === 0) {
        return;
      }

      const sourceIds = [...pending.keys()];
      const runtime = monitorStreamRuntimeRef.current;
      const shouldRefreshOpportunities = [...pending.values()].some(Boolean);
      const runRequests = sourceIds.map(async (sourceId) => {
        const generation = nextRequestGeneration(monitorRunsRequestGenerationRef.current, sourceId);
        const sourceRuns = await fetchRuns({ source_id: sourceId, limit: MONITOR_RUN_HISTORY_LIMIT });
        return [sourceId, sourceRuns, generation] as const;
      });
      const statsRequests = sourceIds.map(async (sourceId) => {
        const generation = nextRequestGeneration(monitorStatsRequestGenerationRef.current, sourceId);
        const range = runtime.statsRanges[sourceId] ?? DEFAULT_MONITOR_STATS_RANGE;
        const stats = await fetchMonitorStats(sourceId, range);
        return [sourceId, stats, generation] as const;
      });
      const [sourceResult, runResults, statsResults, opportunityResult] = await Promise.all([
        settlePromise(fetchSources()),
        Promise.allSettled(runRequests),
        Promise.allSettled(statsRequests),
        settlePromise(
          shouldRefreshOpportunities
            ? fetchOpportunities(buildOpportunityQuery(runtime.opportunityFilters, 1, runtime.opportunitiesPageSize))
            : Promise.resolve(null)
        )
      ]);

      if (sourceResult.status === 'fulfilled') {
        setSources(sourceResult.value);
      }
      const runEntries = runResults
        .filter((result): result is PromiseFulfilledResult<Awaited<(typeof runRequests)[number]>> => result.status === 'fulfilled')
        .map((result) => result.value)
        .filter((entry) => monitorRunsRequestGenerationRef.current.get(entry[0]) === entry[2])
        .map(([sourceId, sourceRuns]) => [sourceId, sourceRuns] as const);
      if (runEntries.length > 0) {
        setMonitorRunsBySource((current) => ({ ...current, ...Object.fromEntries(runEntries) }));
      }
      const statsEntries = statsResults
        .filter((result): result is PromiseFulfilledResult<Awaited<(typeof statsRequests)[number]>> => result.status === 'fulfilled')
        .map((result) => result.value)
        .filter((entry) => monitorStatsRequestGenerationRef.current.get(entry[0]) === entry[2])
        .map(([sourceId, stats]) => [sourceId, stats] as const);
      if (statsEntries.length > 0) {
        setMonitorStatsBySource((current) => ({ ...current, ...Object.fromEntries(statsEntries) }));
      }
      if (opportunityResult.status === 'fulfilled' && opportunityResult.value) {
        setOpportunityPage(opportunityResult.value);
      }

      const failed = sourceResult.status === 'rejected'
        || runResults.some((result) => result.status === 'rejected')
        || statsResults.some((result) => result.status === 'rejected')
        || opportunityResult.status === 'rejected';
      if (failed) {
        pending.forEach((refreshOpportunities, sourceId) => {
          pendingTerminalEvents.set(sourceId, (pendingTerminalEvents.get(sourceId) ?? false) || refreshOpportunities);
        });
        setError('No se pudo actualizar por completo el monitor terminado');
        if (terminalRefreshTimerRef.current === null) {
          terminalRefreshTimerRef.current = window.setTimeout(() => void refreshTerminalBatch(), 3000);
        }
      }
    };

    const scheduleTerminalRefresh = (event: RunEvent) => {
      if (!event.source_id || !isTerminalRunEvent(event.phase)) {
        return;
      }
      const runtime = monitorStreamRuntimeRef.current;
      if (!runtime.sourceIds.has(event.source_id)) {
        return;
      }
      const opportunitiesCreated = event.details?.opportunities_created;
      const refreshOpportunities = typeof opportunitiesCreated !== 'number' || opportunitiesCreated > 0;
      const currentDecision = pendingTerminalEvents.get(event.source_id) ?? false;
      pendingTerminalEvents.set(event.source_id, currentDecision || refreshOpportunities);
      if (terminalRefreshTimerRef.current === null) {
        terminalRefreshTimerRef.current = window.setTimeout(() => void refreshTerminalBatch(), 400);
      }
    };

    function scheduleReconnect() {
      if (disposed || monitorStreamReconnectTimerRef.current !== null) {
        return;
      }
      monitorStreamReconnectTimerRef.current = window.setTimeout(() => {
        monitorStreamReconnectTimerRef.current = null;
        void reconnectIfAuthenticated();
      }, 3000);
    }

    function clearStreamLivenessTimer() {
      if (streamLivenessTimer !== null) {
        window.clearTimeout(streamLivenessTimer);
        streamLivenessTimer = null;
      }
    }

    function armStreamLivenessTimer(currentStream: EventSource) {
      clearStreamLivenessTimer();
      streamLivenessTimer = window.setTimeout(() => {
        streamLivenessTimer = null;
        if (disposed || stream !== currentStream) {
          return;
        }
        setMonitorStreamStatus('error');
        setMonitorStreamReady(false);
        currentStream.close();
        stream = null;
        scheduleReconnect();
      }, MONITOR_STREAM_LIVENESS_TIMEOUT_MS);
    }

    function cancelAuthRevalidation() {
      if (authRevalidationTimer !== null) {
        window.clearTimeout(authRevalidationTimer);
        authRevalidationTimer = null;
      }
      authRevalidationController?.abort();
      authRevalidationController = null;
    }

    async function reconnectIfAuthenticated() {
      if (disposed) {
        return;
      }
      cancelAuthRevalidation();
      const controller = new AbortController();
      authRevalidationController = controller;
      authRevalidationTimer = window.setTimeout(() => controller.abort(), MONITOR_STREAM_AUTH_TIMEOUT_MS);
      try {
        const authenticated = await revalidateLocalAuthentication(controller.signal);
        if (disposed) {
          return;
        }
        if (!authenticated) {
          announceAuthenticationRequired();
          return;
        }
        connect();
      } catch {
        if (!disposed) {
          setMonitorStreamStatus('error');
          scheduleReconnect();
        }
      } finally {
        if (authRevalidationController === controller) {
          if (authRevalidationTimer !== null) {
            window.clearTimeout(authRevalidationTimer);
            authRevalidationTimer = null;
          }
          authRevalidationController = null;
        }
      }
    }

    function connect() {
      if (disposed) {
        return;
      }
      setMonitorStreamStatus('connecting');
      setMonitorStreamReady(false);
      clearStreamLivenessTimer();
      stream?.close();
      const nextStream = new EventSource(
        monitorEventsStreamUrl(monitorStreamCursorRef.current ?? undefined),
        { withCredentials: true }
      );
      stream = nextStream;
      armStreamLivenessTimer(nextStream);
      nextStream.addEventListener('open', () => {
        if (!disposed && stream === nextStream) {
          setMonitorStreamStatus('connected');
          armStreamLivenessTimer(nextStream);
        }
      });
      nextStream.addEventListener('error', () => {
        if (disposed || stream !== nextStream) {
          return;
        }
        setMonitorStreamStatus('error');
        setMonitorStreamReady(false);
        clearStreamLivenessTimer();
        nextStream.close();
        stream = null;
        scheduleReconnect();
      });
      nextStream.addEventListener('stream_ready', (message) => {
        if (disposed || stream !== nextStream) {
          return;
        }
        const ready = parseStreamCursor(message) ?? parseStreamReady(message);
        if (ready !== null) {
          monitorStreamCursorRef.current = Math.max(monitorStreamCursorRef.current ?? 0, ready);
          setMonitorStreamReady(true);
          armStreamLivenessTimer(nextStream);
        }
      });
      nextStream.addEventListener('stream_heartbeat', () => {
        if (!disposed && stream === nextStream) {
          armStreamLivenessTimer(nextStream);
        }
      });
      nextStream.addEventListener('monitor_event', (message) => {
        if (disposed || stream !== nextStream) {
          return;
        }
        const event = parseRunEvent(message);
        if (!event || monitorStreamSeenEventIdsRef.current.has(event.id)) {
          return;
        }
        armStreamLivenessTimer(nextStream);
        monitorStreamSeenEventIdsRef.current.add(event.id);
        const cursor = parseStreamCursor(message);
        if (cursor !== null) {
          monitorStreamCursorRef.current = Math.max(monitorStreamCursorRef.current ?? 0, cursor);
        }
        setMonitorEventsBySource((current) => mergeMonitorEventRecords(current, event));
        scheduleTerminalRefresh(event);
      });
    }

    connect();
    if (pendingTerminalEvents.size > 0 && terminalRefreshTimerRef.current === null) {
      terminalRefreshTimerRef.current = window.setTimeout(() => void refreshTerminalBatch(), 0);
    }

    return () => {
      disposed = true;
      clearStreamLivenessTimer();
      cancelAuthRevalidation();
      stream?.close();
      stream = null;
      setMonitorStreamReady(false);
      if (monitorStreamReconnectTimerRef.current !== null) {
        window.clearTimeout(monitorStreamReconnectTimerRef.current);
        monitorStreamReconnectTimerRef.current = null;
      }
    };
  }, [activeSection]);

  useEffect(() => {
    const pendingTerminalEvents = pendingTerminalEventsRef.current;
    return () => {
      if (terminalRefreshTimerRef.current !== null) {
        window.clearTimeout(terminalRefreshTimerRef.current);
      }
      pendingTerminalEvents.clear();
    };
  }, []);

  const refreshRuntime = useCallback(async (sourceData = sources) => {
    const [opportunityData, runData] = await Promise.all([fetchOpportunities(), fetchRuns()]);
    setOpportunityPage(opportunityData);
    setRuns(runData);
    await refreshLoadedMonitorStats(sourceData);
  }, [refreshLoadedMonitorStats, sources]);

  async function loadOpportunities(page = 1, filters = opportunityFilters, pageSize = opportunitiesPageSize) {
    setLoadingOpportunities(true);
    setError(null);
    try {
      setOpportunityPage(await fetchOpportunities(buildOpportunityQuery(filters, page, pageSize)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudieron cargar las oportunidades');
    } finally {
      setLoadingOpportunities(false);
    }
  }

  async function onCreateSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const created = await createSource({ name: sourceName, url: sourceUrl });
      setSources((current) => [created, ...current]);
      setSourceDrafts((current) => ({ ...current, [created.id]: buildSourceDraft(created) }));
      await loadMonitorStats(created.id, DEFAULT_MONITOR_STATS_RANGE);
      setSourceName('');
      setSourceUrl('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo crear el monitor');
    }
  }

  async function onCreateProxy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSavingProxy(true);
    try {
      const created = await createProxyProfile({
        name: proxyDraft.name,
        scheme: proxyDraft.scheme,
        kind: proxyDraft.kind,
        host: proxyDraft.host,
        port: Number(proxyDraft.port),
        max_concurrent_runs: Number(proxyDraft.maxConcurrentRuns),
        country_code: proxyDraft.countryCode,
        username: proxyDraft.username || undefined,
        password: proxyDraft.password || undefined
      });
      setProxyProfiles((current) => [created, ...current]);
      setProxyDraft(emptyProxyDraft);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo guardar el proxy');
    } finally {
      setSavingProxy(false);
    }
  }

  async function onTestProxy(profileId: number) {
    setError(null);
    setTestingProxyIds((current) => addId(current, profileId));
    setProxyActionMessages((current) => ({ ...current, [profileId]: 'Probando salida IP...' }));
    try {
      const updated = await testProxyProfile(profileId);
      setProxyProfiles((current) => current.map((profile) => (profile.id === updated.id ? updated : profile)));
      setProxyActionMessages((current) => ({
        ...current,
        [profileId]: proxyTestMessage(updated)
      }));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'No se pudo probar el proxy';
      setError(message);
      setProxyActionMessages((current) => ({ ...current, [profileId]: message }));
    } finally {
      setTestingProxyIds((current) => current.filter((id) => id !== profileId));
    }
  }

  async function onToggleProxy(profile: ProxyProfile) {
    setError(null);
    try {
      const updated = await updateProxyProfile(profile.id, { is_active: !profile.is_active });
      setProxyProfiles((current) => current.map((entry) => (entry.id === updated.id ? updated : entry)));
      setScheduler(await fetchScheduler());
    } catch (caught) {
      setScheduler(null);
      setSchedulerAvailabilityError('No se pudo consultar la disponibilidad del scheduler.');
      setError(caught instanceof Error ? caught.message : 'No se pudo actualizar el proxy');
    }
  }

  async function onStartSession(source: SearchSource) {
    const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
    setError(null);
    if (sourceDraftHasChanges(source, draft)) {
      setError('Guarda los cambios antes de lanzar la sesion');
      return;
    }
    if (!source.baseline_ready) {
      setError('Recalibra el listado inicial antes de ejecutar este monitor');
      return;
    }
    if (source.catalog_filter_compatibility && !source.catalog_filter_compatibility.compatible) {
      setError('Corrige los filtros de URL no soportados antes de ejecutar este monitor');
      return;
    }
    if (source.monitor_mode !== 'manual') {
      let schedulerData: SchedulerState;
      try {
        schedulerData = await fetchScheduler();
      } catch {
        setScheduler(null);
        setSchedulerAvailabilityError('No se pudo consultar la disponibilidad del scheduler.');
        setError('No se pudo confirmar que el worker del scheduler este disponible.');
        return;
      }
      setScheduler(schedulerData);
      setSchedulerAvailabilityError(null);
      if (!schedulerData.worker_available) {
        setError('El worker del scheduler no esta disponible. Inicia el worker antes de lanzar una sesion periodica.');
        return;
      }
      if (!schedulerData.effective_enabled) {
        setError('El scheduler no esta operativo. Revisa los ajustes de interfaz, despliegue y capacidad.');
        return;
      }
    }
    setRunningSessionId(source.id);
    try {
      const run = source.monitor_mode === 'manual' ? await runMonitor(source.id) : await startMonitor(source.id);
      const [sourceData, opportunityData, runData] = await Promise.all([
        fetchSources(),
        fetchOpportunities(buildOpportunityQuery(opportunityFilters, 1, opportunitiesPageSize)),
        fetchRuns()
      ]);
      setSources(sourceData);
      setSourceDrafts(buildSourceDrafts(sourceData));
      setRuns([run, ...runData.filter((entry) => entry.id !== run.id)].slice(0, 50));
      setOpportunityPage(opportunityData);
      setMonitorRunsBySource((current) => ({
        ...current,
        [source.id]: [run, ...(current[source.id] ?? []).filter((entry) => entry.id !== run.id)].slice(0, MONITOR_RUN_HISTORY_LIMIT)
      }));
      await loadMonitorStats(source.id);
      if (monitorEventsBySource[source.id]) {
        await loadMonitorEvents(source.id);
      }
    } catch (caught) {
      if (source.monitor_mode !== 'manual') {
        setScheduler(null);
        setSchedulerAvailabilityError('La disponibilidad del scheduler debe volver a comprobarse.');
      }
      setError(caught instanceof Error ? caught.message : 'No se pudo lanzar el monitor');
    } finally {
      setRunningSessionId(null);
    }
  }

  async function onRecalibrateBaseline(source: SearchSource) {
    const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
    setError(null);
    if (sourceDraftHasChanges(source, draft)) {
      setError('Guarda los cambios antes de recalibrar el listado inicial');
      return;
    }
    if (source.catalog_filter_compatibility && !source.catalog_filter_compatibility.compatible) {
      setError('Corrige los filtros de URL no soportados antes de recalibrar el listado inicial');
      return;
    }
    setSavingSourceId(source.id);
    try {
      const run = await calibrateMonitorBaseline(source.id);
      const [sourceData, runData] = await Promise.all([fetchSources(), fetchRuns()]);
      setSources(sourceData);
      setSourceDrafts(buildSourceDrafts(sourceData));
      setRuns([run, ...runData.filter((entry) => entry.id !== run.id)].slice(0, 50));
      setMonitorRunsBySource((current) => ({
        ...current,
        [source.id]: [run, ...(current[source.id] ?? []).filter((entry) => entry.id !== run.id)].slice(0, MONITOR_RUN_HISTORY_LIMIT)
      }));
      await loadMonitorStats(source.id);
      await loadMonitorEvents(source.id);
      if (run.status !== 'success') {
        setError(run.error_message || 'No se pudo recalibrar el listado inicial');
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo recalibrar el listado inicial');
    } finally {
      setSavingSourceId(null);
    }
  }

  async function onPrepareVintedSession(source: SearchSource) {
    const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
    setError(null);
    if (sourceDraftHasChanges(source, draft)) {
      setError('Guarda los cambios antes de preparar la sesion Vinted');
      return;
    }
    if (source.catalog_filter_compatibility && !source.catalog_filter_compatibility.compatible) {
      setError('Corrige los filtros de URL no soportados antes de preparar la sesion Vinted');
      return;
    }
    setSavingSourceId(source.id);
    try {
      const run = await prepareMonitorVintedSession(source.id);
      const [sourceData, runData, proxyData] = await Promise.all([fetchSources(), fetchRuns(), fetchProxyProfiles()]);
      setSources(sourceData);
      setSourceDrafts(buildSourceDrafts(sourceData));
      setRuns([run, ...runData.filter((entry) => entry.id !== run.id)].slice(0, 50));
      setProxyProfiles(proxyData);
      setMonitorRunsBySource((current) => ({
        ...current,
        [source.id]: [run, ...(current[source.id] ?? []).filter((entry) => entry.id !== run.id)].slice(0, MONITOR_RUN_HISTORY_LIMIT)
      }));
      await loadMonitorStats(source.id);
      await loadMonitorEvents(source.id);
      if (run.status !== 'success') {
        setError(run.error_message || 'No se pudo preparar la sesion Vinted');
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo preparar la sesion Vinted');
    } finally {
      setSavingSourceId(null);
    }
  }

  async function onProbeItemDetail(source: SearchSource) {
    const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
    const itemRef = (detailProbeRefs[source.id] ?? '').trim();
    setError(null);
    if (sourceDraftHasChanges(source, draft)) {
      setError('Guarda los cambios antes de probar el detalle de un item');
      return;
    }
    if (source.catalog_filter_compatibility && !source.catalog_filter_compatibility.compatible) {
      setError('Corrige los filtros de URL no soportados antes de probar el detalle de un item');
      return;
    }
    if (!itemRef) {
      setError('Introduce un ID o URL de item Vinted para probar el detalle');
      return;
    }
    setSavingSourceId(source.id);
    setDetailProbeMessages((current) => ({ ...current, [source.id]: 'Probando detalle...' }));
    try {
      const probe = await probeMonitorItemDetail(source.id, itemRef);
      const [sourceData, runData, proxyData] = await Promise.all([fetchSources(), fetchRuns(), fetchProxyProfiles()]);
      setSources(sourceData);
      setSourceDrafts(buildSourceDrafts(sourceData));
      setRuns([probe.run, ...runData.filter((entry) => entry.id !== probe.run.id)].slice(0, 50));
      setProxyProfiles(proxyData);
      setMonitorRunsBySource((current) => ({
        ...current,
        [source.id]: [probe.run, ...(current[source.id] ?? []).filter((entry) => entry.id !== probe.run.id)].slice(0, MONITOR_RUN_HISTORY_LIMIT)
      }));
      await loadMonitorStats(source.id);
      await loadMonitorEvents(source.id);
      setDetailProbeMessages((current) => ({ ...current, [source.id]: detailProbeMessage(probe.result) }));
      if (probe.run.status !== 'success') {
        setError(probe.run.error_message || 'No se pudo probar el detalle del item');
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'No se pudo probar el detalle del item';
      setError(message);
      setDetailProbeMessages((current) => ({ ...current, [source.id]: message }));
    } finally {
      setSavingSourceId(null);
    }
  }

  async function onStopMonitor(sourceId: number) {
    setError(null);
    setSavingSourceId(sourceId);
    try {
      replaceSource(await stopMonitor(sourceId));
      await refreshRuntime();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo parar el monitor');
    } finally {
      setSavingSourceId(null);
    }
  }

  async function onToggleScheduler() {
    if (!scheduler) {
      return;
    }
    setError(null);
    setSavingScheduler(true);
    try {
      setScheduler(await updateScheduler({ enabled: !scheduler.enabled }));
      setSchedulerAvailabilityError(null);
    } catch (caught) {
      setScheduler(null);
      setSchedulerAvailabilityError('No se pudo confirmar el estado del scheduler.');
      setError(caught instanceof Error ? caught.message : 'No se pudo actualizar el scheduler');
    } finally {
      setSavingScheduler(false);
    }
  }

  async function onUpdateSchedulerConfig(payload: SchedulerUpdate) {
    setError(null);
    setSavingScheduler(true);
    try {
      setScheduler(await updateScheduler(payload));
      setSchedulerAvailabilityError(null);
    } catch (caught) {
      setScheduler(null);
      setSchedulerAvailabilityError('No se pudo confirmar el estado del scheduler.');
      setError(caught instanceof Error ? caught.message : 'No se pudo actualizar el scheduler');
    } finally {
      setSavingScheduler(false);
    }
  }

  async function onDeleteSource(source: SearchSource) {
    setError(null);
    setSavingSourceId(source.id);
    try {
      await deleteSource(source.id);
      const remainingSources = sources.filter((entry) => entry.id !== source.id);
      setSources(remainingSources);
      setSourceDrafts((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      setMonitorStatsBySource((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      setMonitorRunsBySource((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      setMonitorEventsBySource((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      setMonitorHiddenEventIdsBySource((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      await refreshRuntime(remainingSources);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo archivar el monitor');
    } finally {
      setSavingSourceId(null);
    }
  }

  async function onSaveSourceSchedule(source: SearchSource) {
    const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
    setError(null);
    setSavingSourceId(source.id);
    try {
      const updated = await saveMonitorConfig(source, draft);
      replaceSource(updated);
      setSourceDrafts((current) => ({
        ...current,
        [updated.id]: {
          ...buildSourceDraft(updated),
          sessionDurationMinutes: (current[updated.id] ?? draft).sessionDurationMinutes
        }
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo guardar el monitor');
    } finally {
      setSavingSourceId(null);
    }
  }

  function replaceSource(updated: SearchSource) {
    setSources((current) => current.map((source) => (source.id === updated.id ? updated : source)));
  }

  function updateSourceDraft(sourceId: number, field: keyof SourceDraft, value: string) {
    setSourceDrafts((current) => ({
      ...current,
      [sourceId]: {
        ...(current[sourceId] ?? {
          monitorMode: 'manual',
          intervalSeconds: '300',
          jitterPercent: '20',
          windowStart: '',
          windowEnd: '',
          sessionDurationMinutes: '60',
          filterTerms: ''
        }),
        [field]: value
      }
    }));
  }

  function updateDetailProbeRef(sourceId: number, value: string) {
    setDetailProbeRefs((current) => ({ ...current, [sourceId]: value }));
    setDetailProbeMessages((current) => {
      if (!current[sourceId]) {
        return current;
      }
      const next = { ...current };
      delete next[sourceId];
      return next;
    });
  }

  function updateOpportunityFilter(field: keyof OpportunityFilters, value: string) {
    setOpportunityFilters((current) => ({ ...current, [field]: value }));
  }

  function clearOpportunityFilters() {
    setOpportunityFilters(defaultOpportunityFilters);
    void loadOpportunities(1, defaultOpportunityFilters, opportunitiesPageSize);
  }

  function changeResultsPageSize(pageSize: number) {
    setOpportunitiesPageSize(pageSize);
    void loadOpportunities(1, opportunityFilters, pageSize);
  }

  async function loadMonitorStats(sourceId: number, range = monitorStatsRangeBySource[sourceId] ?? DEFAULT_MONITOR_STATS_RANGE) {
    setMonitorStatsRangeBySource((current) => ({ ...current, [sourceId]: range }));
    const generation = nextRequestGeneration(monitorStatsRequestGenerationRef.current, sourceId);
    const stats = await fetchMonitorStats(sourceId, range);
    if (monitorStatsRequestGenerationRef.current.get(sourceId) === generation) {
      setMonitorStatsBySource((current) => ({ ...current, [sourceId]: stats }));
    }
  }

  async function loadMonitorRuns(sourceId: number, limit = MONITOR_RUN_HISTORY_LIMIT) {
    const generation = nextRequestGeneration(monitorRunsRequestGenerationRef.current, sourceId);
    const sourceRuns = await fetchRuns({ source_id: sourceId, limit });
    if (monitorRunsRequestGenerationRef.current.get(sourceId) === generation) {
      setMonitorRunsBySource((current) => ({ ...current, [sourceId]: sourceRuns }));
    }
  }

  const loadMonitorEvents = useCallback(async (sourceId: number) => {
    try {
      const events = await fetchMonitorEvents(sourceId);
      setMonitorEventsBySource((current) => ({
        ...current,
        [sourceId]: mergeRunEvents(events, current[sourceId] ?? [])
      }));
      setMonitorEventHistoryLoadedBySource((current) => ({ ...current, [sourceId]: true }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudieron cargar los logs acumulados');
    }
  }, []);

  const appendMonitorEvent = useCallback((event: RunEvent) => {
    const sourceId = event.source_id;
    if (!sourceId) {
      return;
    }
    setMonitorEventsBySource((current) => mergeMonitorEventRecords(current, event));
  }, []);

  const clearMonitorEventsView = useCallback((sourceId: number, visibleEventIds: number[]) => {
    if (visibleEventIds.length === 0) {
      return;
    }
    setMonitorHiddenEventIdsBySource((current) => {
      const hiddenIds = new Set(current[sourceId] ?? []);
      let changed = false;
      visibleEventIds.forEach((eventId) => {
        if (!hiddenIds.has(eventId)) {
          hiddenIds.add(eventId);
          changed = true;
        }
      });
      if (!changed) {
        return current;
      }
      return { ...current, [sourceId]: [...hiddenIds].sort((left, right) => left - right) };
    });
  }, []);

  function getSourceName(sourceId: number): string {
    return sources.find((source) => source.id === sourceId)?.name ?? `Monitor ${sourceId}`;
  }

  function selectSection(section: string) {
    setActiveSection(section);
    window.setTimeout(() => window.scrollTo({ top: 0, left: 0 }), 0);
  }

  return {
    activeSection,
    activeSubtitle,
    activeTitle,
    changeResultsPageSize,
    clearOpportunityFilters,
    detailProbeMessages,
    detailProbeRefs,
    error,
    getSourceName,
    loadOpportunities,
    loadMonitorEvents,
    loadMonitorStats,
    loadMonitorRuns,
    loadingOpportunities,
    navCollapsed,
    onCreateProxy,
    onCreateSource,
    onDeleteSource,
    onAppendMonitorEvent: appendMonitorEvent,
    onClearMonitorEventsView: clearMonitorEventsView,
    onLoadRunEvents: fetchRunEvents,
    onSaveSourceSchedule,
    onPrepareVintedSession,
    onProbeItemDetail,
    onRecalibrateBaseline,
    onStartSession,
    onStopMonitor,
    onTestProxy,
    onToggleProxy,
    onToggleScheduler,
    onUpdateSchedulerConfig,
    monitorStatsBySource,
    monitorStatsRangeBySource,
    monitorRunsBySource,
    monitorEventsBySource,
    monitorEventHistoryLoadedBySource,
    monitorHiddenEventIdsBySource,
    monitorStreamStatus,
    monitorStreamReady,
    opportunityPage,
    proxyDraft,
    proxyProfiles,
    proxyActionMessages,
    refreshRuntime,
    opportunityFilters,
    opportunitiesPageSize,
    runningSessionId,
    savingProxy,
    savingScheduler,
    savingSourceId,
    scheduler,
    schedulerAvailabilityError,
    selectSection,
    setNavCollapsed,
    setProxyDraft,
    setSourceName,
    setSourceUrl,
    sourceDrafts,
    sourceName,
    sources,
    sourceUrl,
    testingProxyIds,
    runs,
    updateOpportunityFilter,
    updateDetailProbeRef,
    updateSourceDraft
  };

  async function saveMonitorConfig(source: SearchSource, draft: SourceDraft, precomputedAllowedWindows?: string[] | null) {
    const allowedWindows = precomputedAllowedWindows ?? (draft.monitorMode === 'window' ? buildAllowedWindows(draft) : []);
    if (allowedWindows === null) {
      throw new Error('Configura una hora de inicio y fin validas');
    }
    const isRecurring = draft.monitorMode !== 'manual';
    const intervalSeconds = isRecurring
      ? parseIntegerInRange(draft.intervalSeconds, 'El intervalo', 60, 3600)
      : (source.scheduler_config.interval_seconds ?? 300);
    const jitterPercent = isRecurring
      ? parseIntegerInRange(draft.jitterPercent, 'El jitter', 0, 50)
      : (source.scheduler_config.jitter_percent ?? 20);
    const stopAfterVintedSessionUses =
      isRecurring && draft.stopAfterVintedSessionUses.trim()
        ? parseIntegerInRange(draft.stopAfterVintedSessionUses, 'El limite de usos de sesion Vinted', 1, 1000)
        : null;
    const durationMinutes =
      draft.monitorMode === 'duration' ? parseIntegerInRange(draft.sessionDurationMinutes, 'La duracion del monitor', 1, 1440) : null;
    return updateSource(source.id, {
      monitor_mode: draft.monitorMode,
      duration_minutes: durationMinutes,
      filter_definition: { blacklist_terms: parseFilterTerms(draft.filterTerms) },
      scheduler_config: {
        interval_seconds: intervalSeconds,
        jitter_percent: jitterPercent,
        allowed_windows: draft.monitorMode === 'window' ? allowedWindows : [],
        stop_after_vinted_session_uses: stopAfterVintedSessionUses
      }
    });
  }
}

function detailProbeMessage(result: Record<string, unknown>): string {
  const outcome = typeof result.outcome === 'string' ? result.outcome : 'unknown';
  const status = typeof result.status_code === 'number' ? `status=${result.status_code}` : null;
  const duration = typeof result.duration_ms === 'number' ? `ms=${result.duration_ms}` : null;
  const summary = recordValue(result.detail_summary);
  const photos = numberOrString(summary?.photo_count);
  const description = numberOrString(summary?.description_length);
  const parser = typeof summary?.parser_version === 'string' ? `parser=${summary.parser_version}` : null;
  const availability = typeof summary?.availability_state === 'string' ? `availability=${summary.availability_state}` : null;
  const missing = Array.isArray(summary?.missing_required)
    ? summary.missing_required.filter((field): field is string => typeof field === 'string').join(',')
    : '';
  const tokens = [
    status,
    duration,
    parser,
    photos ? `photos=${photos}` : null,
    description ? `description_chars=${description}` : null,
    availability,
    missing ? `missing=${missing}` : null
  ]
    .filter(Boolean)
    .join(' ');
  return tokens ? `Detalle ${outcome}: ${tokens}` : `Detalle ${outcome}`;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function numberOrString(value: unknown): string | null {
  if (typeof value === 'number' || typeof value === 'string') {
    return String(value);
  }
  return null;
}

function addId(current: number[], id: number): number[] {
  return current.includes(id) ? current : [...current, id];
}

function proxyTestMessage(profile: ProxyProfile): string {
  if (profile.last_test_status === 'success') {
    return `Test IP correcto: ${profile.last_test_ip ?? 'IP no informada'}`;
  }
  if (profile.last_test_status === 'failed') {
    return `Test IP fallido: ${profile.last_test_error ?? 'sin detalle'}`;
  }
  return 'Test IP completado sin estado';
}

function sectionSubtitle(section: string, opportunityTotal: number, sourceTotal: number): string {
  if (section === 'opportunities') {
    return `${opportunityTotal} oportunidades`;
  }
  if (section === 'sources') {
    return `${sourceTotal} monitores configurados`;
  }
  if (section === 'settings') {
    return 'Configuracion local del monitor';
  }
  return `${opportunityTotal} oportunidades`;
}

function buildAllowedWindows(draft: SourceDraft): string[] | null {
  const start = draft.windowStart.trim();
  const end = draft.windowEnd.trim();
  if (!start && !end) {
    return [];
  }
  if (!isValidTimeInput(start) || !isValidTimeInput(end) || start === end) {
    return null;
  }
  return [`${start}-${end}`];
}

function parseIntegerInRange(value: string, label: string, minimum: number, maximum: number): number {
  if (!/^\d+$/.test(value.trim())) {
    throw new Error(`${label} debe ser un numero entero entre ${minimum} y ${maximum}`);
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${label} debe estar entre ${minimum} y ${maximum}`);
  }
  return parsed;
}

function isValidTimeInput(value: string): boolean {
  if (!/^\d{2}:\d{2}$/.test(value)) {
    return false;
  }
  const [hours, minutes] = value.split(':').map(Number);
  return hours >= 0 && hours <= 23 && minutes >= 0 && minutes <= 59;
}

function parseRunEvent(message: MessageEvent): RunEvent | null {
  try {
    return JSON.parse(message.data) as RunEvent;
  } catch {
    return null;
  }
}

function parseStreamReady(message: MessageEvent): number | null {
  try {
    const payload = JSON.parse(message.data) as { last_event_id?: unknown };
    return typeof payload.last_event_id === 'number' ? payload.last_event_id : null;
  } catch {
    return null;
  }
}

function parseStreamCursor(message: MessageEvent): number | null {
  if (!message.lastEventId || !/^\d+$/.test(message.lastEventId)) {
    return null;
  }
  return Number(message.lastEventId);
}

function nextRequestGeneration(generations: Map<number, number>, sourceId: number): number {
  const generation = (generations.get(sourceId) ?? 0) + 1;
  generations.set(sourceId, generation);
  return generation;
}

async function settlePromise<T>(promise: Promise<T>): Promise<PromiseSettledResult<T>> {
  try {
    return { status: 'fulfilled', value: await promise };
  } catch (reason) {
    return { status: 'rejected', reason };
  }
}

function isTerminalRunEvent(phase: string): boolean {
  return phase === 'run_succeeded' || phase === 'run_failed';
}

function mergeRunEvents(...eventGroups: RunEvent[][]): RunEvent[] {
  const eventsById = new Map<number, RunEvent>();
  eventGroups.forEach((events) => events.forEach((event) => eventsById.set(event.id, event)));
  return [...eventsById.values()].sort((left, right) => left.id - right.id);
}

function mergeMonitorEventRecords(current: Record<number, RunEvent[]>, event: RunEvent): Record<number, RunEvent[]> {
  if (!event.source_id) {
    return current;
  }
  const existing = current[event.source_id] ?? [];
  if (existing.some((entry) => entry.id === event.id)) {
    return current;
  }
  return { ...current, [event.source_id]: mergeRunEvents(existing, [event]) };
}
