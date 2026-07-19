import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  announceAuthenticationRequired,
  createProxyProfile,
  createSource,
  deleteSource,
  fetchMonitorEvents,
  fetchMonitorStats,
  fetchOpportunities,
  fetchProxyProfiles,
  fetchRuns,
  fetchScheduler,
  fetchSources,
  monitorEventsStreamUrl,
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
  type OpportunityQuery,
  type OpportunityResult,
  type Page,
  type ProxyProfile,
  type SchedulerUpdate,
  type Run,
  type RunEvent,
  type SchedulerState,
  type SearchSource
} from '../api';
import { markCollectionUnavailable, type CollectionLoadState } from '../app/collectionLoadState';
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
const MONITOR_RUN_STATE_LOAD_ERROR = 'No se pudo comprobar el estado de ejecucion del monitor; recarga Monitores para reintentar';
const STOP_MONITOR_STATE_REFRESH_ERROR = 'La sesion se detuvo, pero no se pudo confirmar por completo su estado; recarga Monitores';
const CREATE_MONITOR_STATE_REFRESH_ERROR = 'El monitor se creo, pero no se pudieron cargar sus estadisticas; recarga Monitores';
const START_MONITOR_STATE_REFRESH_ERROR = 'La sesion se inicio, pero no se pudo actualizar por completo su estado; recarga Monitores';
const RUN_MONITOR_STATE_REFRESH_ERROR = 'La ejecucion termino, pero no se pudo actualizar por completo su estado; recarga Monitores';
const ARCHIVE_MONITOR_STATE_REFRESH_ERROR = 'El monitor se archivo, pero no se pudieron actualizar por completo los datos derivados; recarga la PWA';

type MonitorCommandKind = 'create' | 'save' | 'start' | 'run' | 'stop' | 'archive';

type MonitorCommand = {
  kind: MonitorCommandKind;
  sourceId: number | null;
};

type PendingSourceNavigation =
  | { kind: 'monitor'; sourceId: number }
  | { kind: 'section'; section: string };

export function useDashboardController() {
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [proxyProfiles, setProxyProfiles] = useState<ProxyProfile[]>([]);
  const [opportunityPage, setOpportunityPage] = useState<Page<OpportunityResult>>(emptyOpportunityPage);
  const [sourceCollectionState, setSourceCollectionState] = useState<CollectionLoadState>('loading');
  const [opportunityCollectionState, setOpportunityCollectionState] = useState<CollectionLoadState>('loading');
  const [proxyCollectionState, setProxyCollectionState] = useState<CollectionLoadState>('loading');
  const [monitorRunsBySource, setMonitorRunsBySource] = useState<Record<number, Run[]>>({});
  const [monitorEventsBySource, setMonitorEventsBySource] = useState<Record<number, RunEvent[]>>({});
  const [monitorEventHistoryLoadedBySource, setMonitorEventHistoryLoadedBySource] = useState<Record<number, boolean>>({});
  const [monitorHiddenEventIdsBySource, setMonitorHiddenEventIdsBySource] = useState<Record<number, number[]>>({});
  const [monitorStatsBySource, setMonitorStatsBySource] = useState<Record<number, MonitorStats>>({});
  const [monitorStatsRangeBySource, setMonitorStatsRangeBySource] = useState<Record<number, MonitorStatsRange>>({});
  const [scheduler, setScheduler] = useState<SchedulerState | null>(null);
  const [schedulerAvailabilityError, setSchedulerAvailabilityError] = useState<string | null>(null);
  const [sourceDrafts, setSourceDrafts] = useState<Record<number, SourceDraft>>({});
  const [requestedSelectedMonitorId, setRequestedSelectedMonitorId] = useState<number | null>(null);
  const [editingSourceId, setEditingSourceId] = useState<number | null>(null);
  const [pendingSourceNavigation, setPendingSourceNavigation] = useState<PendingSourceNavigation | null>(null);
  const [opportunityFilters, setOpportunityFilters] = useState<OpportunityFilters>(defaultOpportunityFilters);
  const [opportunitiesPageSize, setOpportunitiesPageSize] = useState(25);
  const [monitorCommand, setMonitorCommand] = useState<MonitorCommand | null>(null);
  const [pendingStopSourceIds, setPendingStopSourceIds] = useState<number[]>([]);
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
  const fetchOpportunityCollection = useCallback(async (query: OpportunityQuery = {}) => {
    try {
      const opportunityData = await fetchOpportunities(query);
      setOpportunityPage(opportunityData);
      setOpportunityCollectionState('ready');
      return opportunityData;
    } catch (caught) {
      setOpportunityCollectionState(markCollectionUnavailable);
      throw caught;
    }
  }, []);
  const activeTitle = useMemo(() => navItems.find((item) => item.id === activeSection)?.label ?? 'Oportunidades', [activeSection]);
  const activeSubtitle = useMemo(
    () => sectionSubtitle(activeSection, opportunityPage.total, sources.length, opportunityCollectionState, sourceCollectionState),
    [activeSection, opportunityCollectionState, opportunityPage.total, sourceCollectionState, sources.length]
  );
  const monitorStreamCursorRef = useRef<number | null>(null);
  const monitorCommandRef = useRef<MonitorCommand | null>(null);
  const monitorStreamSeenEventIdsRef = useRef<Set<number>>(new Set());
  const pendingTerminalEventsRef = useRef<Map<number, boolean>>(new Map());
  const terminalRefreshTimerRef = useRef<number | null>(null);
  const monitorStreamReconnectTimerRef = useRef<number | null>(null);
  const monitorRunsRequestGenerationRef = useRef<Map<number, number>>(new Map());
  const monitorStatsRequestGenerationRef = useRef<Map<number, number>>(new Map());
  const monitorEventsRequestGenerationRef = useRef<Map<number, number>>(new Map());
  const sourceListRequestGenerationRef = useRef(0);
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
  const monitorCommandPending = monitorCommand !== null;
  const runningSessionId = monitorCommand && (monitorCommand.kind === 'start' || monitorCommand.kind === 'run')
    ? monitorCommand.sourceId
    : null;
  const savingSourceId = monitorCommand?.sourceId ?? null;

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
    const failedSurfaces = new Set<string>();
    const reportBootstrapFailure = (surface: string) => {
      if (disposed) {
        return;
      }
      failedSurfaces.add(surface);
      setError(`Carga inicial incompleta: ${[...failedSurfaces].join(', ')}. Las demas secciones disponibles siguen operativas.`);
    };
    sourceListRequestGenerationRef.current += 1;
    const sourceRequestGeneration = sourceListRequestGenerationRef.current;
    void fetchSources()
      .then((sourceData) => {
        if (!disposed && sourceListRequestGenerationRef.current === sourceRequestGeneration) {
          setSources(sourceData);
          setSourceDrafts(buildSourceDrafts(sourceData));
          setSourceCollectionState('ready');
        }
      })
      .catch(() => {
        if (!disposed && sourceListRequestGenerationRef.current === sourceRequestGeneration) {
          setSourceCollectionState(markCollectionUnavailable);
          reportBootstrapFailure('monitores');
        }
      });
    void fetchOpportunities()
      .then((opportunityData) => {
        if (!disposed) {
          setOpportunityPage(opportunityData);
          setOpportunityCollectionState('ready');
        }
      })
      .catch(() => {
        if (!disposed) {
          setOpportunityCollectionState(markCollectionUnavailable);
        }
        reportBootstrapFailure('oportunidades');
      });
    void fetchProxyProfiles()
      .then((proxyData) => {
        if (!disposed) {
          setProxyProfiles(proxyData);
          setProxyCollectionState('ready');
        }
      })
      .catch(() => {
        if (!disposed) {
          setProxyCollectionState(markCollectionUnavailable);
        }
        reportBootstrapFailure('proxies');
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
    sourceListRequestGenerationRef.current += 1;
    const sourceRequestGeneration = sourceListRequestGenerationRef.current;
    void fetchSources()
      .then((sourceData) => {
        if (!disposed && sourceListRequestGenerationRef.current === sourceRequestGeneration) {
          setSources(sourceData);
          setSourceDrafts((current) => mergeMissingSourceDrafts(current, sourceData));
          setSourceCollectionState('ready');
        }
      })
      .catch((caught: unknown) => {
        if (!disposed && sourceListRequestGenerationRef.current === sourceRequestGeneration) {
          setSourceCollectionState(markCollectionUnavailable);
          setSources((current) => markPreparedSessionsExpired(current, Date.now()));
          setError(caught instanceof Error ? caught.message : 'No se pudo actualizar el estado de los monitores');
        }
      });

    return () => {
      disposed = true;
    };
  }, [activeSection]);

  useEffect(() => {
    if (activeSection !== 'sources') {
      return undefined;
    }

    const now = Date.now();
    const nearestExpiry = sources
      .flatMap((source) => source.prepared_sessions)
      .filter((session) => session.usable_now)
      .map((session) => session.expires_at ? Date.parse(session.expires_at) : Number.NaN)
      .filter((expiresAt) => Number.isFinite(expiresAt) && expiresAt > now)
      .sort((left, right) => left - right)[0];
    if (nearestExpiry === undefined) {
      return undefined;
    }

    let disposed = false;
    const timer = window.setTimeout(() => {
      sourceListRequestGenerationRef.current += 1;
      const sourceRequestGeneration = sourceListRequestGenerationRef.current;
      void fetchSources()
        .then((sourceData) => {
          if (!disposed && sourceListRequestGenerationRef.current === sourceRequestGeneration) {
            setSources(sourceData);
            setSourceCollectionState('ready');
          }
        })
        .catch((caught: unknown) => {
          if (!disposed && sourceListRequestGenerationRef.current === sourceRequestGeneration) {
            setSourceCollectionState(markCollectionUnavailable);
            setSources((current) => markPreparedSessionsExpired(current, Date.now()));
            setError(caught instanceof Error ? caught.message : 'No se pudo actualizar la expiracion de la sesion preparada');
          }
        });
    }, Math.max(0, nearestExpiry - now + 100));

    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [activeSection, sources]);

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
      sourceListRequestGenerationRef.current += 1;
      const sourceRequestGeneration = sourceListRequestGenerationRef.current;
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
            ? fetchOpportunityCollection(buildOpportunityQuery(runtime.opportunityFilters, 1, runtime.opportunitiesPageSize))
            : Promise.resolve(null)
        )
      ]);

      if (
        sourceResult.status === 'fulfilled'
        && sourceListRequestGenerationRef.current === sourceRequestGeneration
      ) {
        setSources(sourceResult.value);
        setSourceCollectionState('ready');
      } else if (
        sourceResult.status === 'rejected'
        && sourceListRequestGenerationRef.current === sourceRequestGeneration
      ) {
        setSourceCollectionState(markCollectionUnavailable);
      }
      const runEntries = runResults
        .filter((result): result is PromiseFulfilledResult<Awaited<(typeof runRequests)[number]>> => result.status === 'fulfilled')
        .map((result) => result.value)
        .filter((entry) => monitorRunsRequestGenerationRef.current.get(entry[0]) === entry[2])
        .map(([sourceId, sourceRuns]) => [sourceId, sourceRuns] as const);
      if (runEntries.length > 0) {
        setMonitorRunsBySource((current) => ({ ...current, ...Object.fromEntries(runEntries) }));
        const refreshedSourceIds = new Set(runEntries.map(([sourceId]) => sourceId));
        setPendingStopSourceIds((current) => current.filter((sourceId) => !refreshedSourceIds.has(sourceId)));
        setError((current) => (
          current === MONITOR_RUN_STATE_LOAD_ERROR || current === STOP_MONITOR_STATE_REFRESH_ERROR ? null : current
        ));
      }
      const statsEntries = statsResults
        .filter((result): result is PromiseFulfilledResult<Awaited<(typeof statsRequests)[number]>> => result.status === 'fulfilled')
        .map((result) => result.value)
        .filter((entry) => monitorStatsRequestGenerationRef.current.get(entry[0]) === entry[2])
        .map(([sourceId, stats]) => [sourceId, stats] as const);
      if (statsEntries.length > 0) {
        setMonitorStatsBySource((current) => ({ ...current, ...Object.fromEntries(statsEntries) }));
      }
      const failed = sourceResult.status === 'rejected'
        || runResults.some((result) => result.status === 'rejected')
        || statsResults.some((result) => result.status === 'rejected')
        || opportunityResult.status === 'rejected';
      if (failed) {
        pending.forEach((refreshOpportunities, sourceId) => {
          if (monitorStreamRuntimeRef.current.sourceIds.has(sourceId)) {
            pendingTerminalEvents.set(sourceId, (pendingTerminalEvents.get(sourceId) ?? false) || refreshOpportunities);
          }
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
  }, [activeSection, fetchOpportunityCollection]);

  useEffect(() => {
    const pendingTerminalEvents = pendingTerminalEventsRef.current;
    return () => {
      if (terminalRefreshTimerRef.current !== null) {
        window.clearTimeout(terminalRefreshTimerRef.current);
      }
      pendingTerminalEvents.clear();
    };
  }, []);

  useEffect(() => {
    if (editingSourceId === null) {
      return;
    }
    const source = sources.find((entry) => entry.id === editingSourceId);
    const runs = monitorRunsBySource[editingSourceId] ?? [];
    const editBecameUnavailable = !source
      || source.is_active
      || pendingStopSourceIds.includes(editingSourceId)
      || runs.some((run) => run.status === 'running' || run.status === 'finalizing');
    if (!editBecameUnavailable) {
      return;
    }
    const timer = window.setTimeout(() => {
      if (source) {
        setSourceDrafts((current) => ({ ...current, [source.id]: buildSourceDraft(source) }));
      }
      setEditingSourceId(null);
      setPendingSourceNavigation(null);
      setError('La edicion se cerro porque el monitor dejo de estar detenido e inactivo');
    }, 0);
    return () => window.clearTimeout(timer);
  }, [editingSourceId, monitorRunsBySource, pendingStopSourceIds, sources]);

  const refreshRuntime = useCallback(async (sourceData = sources) => {
    await fetchOpportunityCollection();
    await refreshLoadedMonitorStats(sourceData);
  }, [fetchOpportunityCollection, refreshLoadedMonitorStats, sources]);

  function beginMonitorCommand(kind: MonitorCommandKind, sourceId: number | null): MonitorCommand | null {
    if (monitorCommandRef.current !== null) {
      return null;
    }
    const command = { kind, sourceId };
    monitorCommandRef.current = command;
    setMonitorCommand(command);
    return command;
  }

  function finishMonitorCommand(command: MonitorCommand) {
    if (monitorCommandRef.current !== command) {
      return;
    }
    monitorCommandRef.current = null;
    setMonitorCommand(null);
  }

  async function loadOpportunities(page = 1, filters = opportunityFilters, pageSize = opportunitiesPageSize) {
    setLoadingOpportunities(true);
    setError(null);
    try {
      await fetchOpportunityCollection(buildOpportunityQuery(filters, page, pageSize));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudieron cargar las oportunidades');
    } finally {
      setLoadingOpportunities(false);
    }
  }

  async function onCreateSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sourceCollectionState !== 'ready') {
      setError('No se puede crear un monitor hasta confirmar la coleccion de monitores');
      return;
    }
    const command = beginMonitorCommand('create', null);
    if (!command) {
      return;
    }
    setError(null);
    try {
      const created = await createSource({ name: sourceName, url: sourceUrl });
      sourceListRequestGenerationRef.current += 1;
      setSources((current) => [created, ...current]);
      setSourceDrafts((current) => ({ ...current, [created.id]: buildSourceDraft(created) }));
      setSourceName('');
      setSourceUrl('');
      try {
        await loadMonitorStats(created.id, DEFAULT_MONITOR_STATS_RANGE);
      } catch {
        setError(CREATE_MONITOR_STATE_REFRESH_ERROR);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo crear el monitor');
    } finally {
      finishMonitorCommand(command);
    }
  }

  async function onCreateProxy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (proxyCollectionState !== 'ready') {
      setError('No se puede crear un proxy hasta confirmar la coleccion de proxys');
      return;
    }
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
    const command = beginMonitorCommand('start', source.id);
    if (!command) {
      return;
    }
    setError(null);
    try {
      if (sourceDraftHasChanges(source, draft)) {
        setError('Guarda los cambios antes de lanzar la sesion');
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
          setError('El scheduler no esta operativo. Revisa el despliegue y la capacidad.');
          return;
        }
      }
      const run = await startMonitor(source.id);
      const refreshComplete = await refreshMonitorCommandResult(source.id, run);
      if (run.status !== 'success') {
        setError(run.error_message || 'No se pudo iniciar la sesion');
      } else if (!refreshComplete) {
        setError(START_MONITOR_STATE_REFRESH_ERROR);
      }
    } catch (caught) {
      if (source.monitor_mode !== 'manual') {
        setScheduler(null);
        setSchedulerAvailabilityError('La disponibilidad del scheduler debe volver a comprobarse.');
      }
      setError(caught instanceof Error ? caught.message : 'No se pudo lanzar el monitor');
    } finally {
      finishMonitorCommand(command);
    }
  }

  async function onRunNow(source: SearchSource) {
    const command = beginMonitorCommand('run', source.id);
    if (!command) {
      return;
    }
    setError(null);
    try {
      if (!source.is_active || source.monitor_mode !== 'manual') {
        setError('Inicia una sesion manual antes de ejecutar el monitor');
        return;
      }
      const run = await runMonitor(source.id);
      const refreshComplete = await refreshMonitorCommandResult(source.id, run);
      if (run.status !== 'success') {
        setError(run.error_message || 'La ejecucion manual ha fallado');
      } else if (!refreshComplete) {
        setError(RUN_MONITOR_STATE_REFRESH_ERROR);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo ejecutar el monitor');
    } finally {
      finishMonitorCommand(command);
    }
  }

  async function refreshMonitorCommandResult(sourceId: number, run: Run): Promise<boolean> {
    recordMonitorRun(sourceId, run);
    sourceListRequestGenerationRef.current += 1;
    const sourceRequestGeneration = sourceListRequestGenerationRef.current;
    const sourceResultPromise = settlePromise(fetchSources());
    const refreshes: Promise<unknown>[] = [loadMonitorStats(sourceId)];
    if (monitorEventsBySource[sourceId]) {
      refreshes.push(loadMonitorEvents(sourceId));
    }
    if (run.opportunities_created > 0) {
      refreshes.push(
        fetchOpportunityCollection(buildOpportunityQuery(opportunityFilters, 1, opportunitiesPageSize))
      );
    }
    const [sourceResult, refreshResults] = await Promise.all([
      sourceResultPromise,
      Promise.allSettled(refreshes)
    ]);
    if (
      sourceResult.status === 'fulfilled'
      && sourceListRequestGenerationRef.current === sourceRequestGeneration
    ) {
      setSources(sourceResult.value);
      setSourceDrafts(buildSourceDrafts(sourceResult.value));
      setSourceCollectionState('ready');
    } else if (
      sourceResult.status === 'rejected'
      && sourceListRequestGenerationRef.current === sourceRequestGeneration
    ) {
      setSourceCollectionState(markCollectionUnavailable);
    }
    return sourceResult.status === 'fulfilled'
      && refreshResults.every((result) => result.status === 'fulfilled');
  }

  function recordMonitorRun(sourceId: number, run: Run) {
    setMonitorRunsBySource((current) => ({
      ...current,
      [sourceId]: [run, ...(current[sourceId] ?? []).filter((entry) => entry.id !== run.id)].slice(0, MONITOR_RUN_HISTORY_LIMIT)
    }));
  }

  async function onStopMonitor(sourceId: number) {
    const command = beginMonitorCommand('stop', sourceId);
    if (!command) {
      return;
    }
    setError(null);
    try {
      const stoppedSource = await stopMonitor(sourceId);
      setPendingStopSourceIds((current) => current.includes(sourceId) ? current : [...current, sourceId]);
      replaceSource(stoppedSource);
      const refreshResults = await Promise.allSettled([
        loadMonitorRuns(sourceId),
        loadMonitorStats(sourceId)
      ]);
      if (refreshResults.some((result) => result.status === 'rejected')) {
        setError(STOP_MONITOR_STATE_REFRESH_ERROR);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo parar el monitor');
    } finally {
      finishMonitorCommand(command);
    }
  }

  async function onUpdateSchedulerConfig(payload: SchedulerUpdate) {
    setError(null);
    try {
      setScheduler(await updateScheduler(payload));
      setSchedulerAvailabilityError(null);
    } catch (caught) {
      setScheduler(null);
      setSchedulerAvailabilityError('No se pudo confirmar el estado del scheduler.');
      setError(caught instanceof Error ? caught.message : 'No se pudo actualizar el scheduler');
    }
  }

  async function onDeleteSource(source: SearchSource) {
    const command = beginMonitorCommand('archive', source.id);
    if (!command) {
      return;
    }
    setError(null);
    try {
      await deleteSource(source.id);
      sourceListRequestGenerationRef.current += 1;
      const remainingSources = sources.filter((entry) => entry.id !== source.id);
      setSources((current) => current.filter((entry) => entry.id !== source.id));
      removeSourceLocalState(source.id);
      try {
        await refreshRuntime(remainingSources);
      } catch {
        setError(ARCHIVE_MONITOR_STATE_REFRESH_ERROR);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo archivar el monitor');
    } finally {
      finishMonitorCommand(command);
    }
  }

  async function onSaveSourceSchedule(source: SearchSource) {
    const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
    const command = beginMonitorCommand('save', source.id);
    if (!command) {
      return;
    }
    setError(null);
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
      setEditingSourceId(null);
      setPendingSourceNavigation(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo guardar el monitor');
    } finally {
      finishMonitorCommand(command);
    }
  }

  function replaceSource(updated: SearchSource) {
    setSources((current) => current.map((source) => (source.id === updated.id ? updated : source)));
  }

  function removeSourceLocalState(sourceId: number) {
    setSourceDrafts((current) => withoutSource(current, sourceId));
    setMonitorStatsBySource((current) => withoutSource(current, sourceId));
    setMonitorStatsRangeBySource((current) => withoutSource(current, sourceId));
    setMonitorRunsBySource((current) => withoutSource(current, sourceId));
    setMonitorEventsBySource((current) => withoutSource(current, sourceId));
    setMonitorEventHistoryLoadedBySource((current) => withoutSource(current, sourceId));
    setMonitorHiddenEventIdsBySource((current) => withoutSource(current, sourceId));
    setPendingStopSourceIds((current) => current.filter((pendingSourceId) => pendingSourceId !== sourceId));
    pendingTerminalEventsRef.current.delete(sourceId);
    monitorRunsRequestGenerationRef.current.delete(sourceId);
    monitorStatsRequestGenerationRef.current.delete(sourceId);
    monitorEventsRequestGenerationRef.current.delete(sourceId);
    if (editingSourceId === sourceId) {
      setEditingSourceId(null);
      setPendingSourceNavigation(null);
    }
    if (requestedSelectedMonitorId === sourceId) {
      setRequestedSelectedMonitorId(null);
    }
  }

  function beginSourceEdit(source: SearchSource) {
    const runs = monitorRunsBySource[source.id];
    const hasNonTerminalRun = runs?.some((run) => run.status === 'running' || run.status === 'finalizing') ?? false;
    const isDraining = pendingStopSourceIds.includes(source.id) || hasNonTerminalSessionRun(runs ?? []);
    if (monitorCommandRef.current || source.is_active || isDraining || runs === undefined || hasNonTerminalRun) {
      setError('La configuracion solo se puede modificar con el monitor detenido y sin ejecuciones pendientes');
      return;
    }
    setError(null);
    setSourceDrafts((current) => ({ ...current, [source.id]: buildSourceDraft(source) }));
    setRequestedSelectedMonitorId(source.id);
    setPendingSourceNavigation(null);
    setEditingSourceId(source.id);
  }

  function cancelSourceEdit() {
    resetSourceEditDraft();
    setEditingSourceId(null);
    setPendingSourceNavigation(null);
  }

  function requestMonitorSelection(sourceId: number) {
    if (editingSourceId === sourceId) {
      setRequestedSelectedMonitorId(sourceId);
      return;
    }
    if (monitorCommandRef.current?.kind === 'save') {
      setError('Espera a que termine el guardado antes de cambiar de monitor');
      return;
    }
    if (sourceEditHasChanges()) {
      setPendingSourceNavigation({ kind: 'monitor', sourceId });
      return;
    }
    resetSourceEditDraft();
    setEditingSourceId(null);
    setPendingSourceNavigation(null);
    setRequestedSelectedMonitorId(sourceId);
  }

  function confirmDiscardSourceEdit() {
    const pending = pendingSourceNavigation;
    resetSourceEditDraft();
    setEditingSourceId(null);
    setPendingSourceNavigation(null);
    if (pending?.kind === 'monitor') {
      setRequestedSelectedMonitorId(pending.sourceId);
    } else if (pending?.kind === 'section') {
      activateSection(pending.section);
    }
  }

  function keepSourceEditing() {
    setPendingSourceNavigation(null);
  }

  function sourceEditHasChanges(): boolean {
    if (editingSourceId === null) {
      return false;
    }
    const source = sources.find((entry) => entry.id === editingSourceId);
    const draft = sourceDrafts[editingSourceId];
    return Boolean(source && draft && sourceDraftHasChanges(source, draft));
  }

  function resetSourceEditDraft() {
    if (editingSourceId === null) {
      return;
    }
    const source = sources.find((entry) => entry.id === editingSourceId);
    if (source) {
      setSourceDrafts((current) => ({ ...current, [source.id]: buildSourceDraft(source) }));
    }
  }

  function updateSourceDraft(sourceId: number, field: keyof SourceDraft, value: string) {
    setSourceDrafts((current) => ({
      ...current,
      [sourceId]: {
        ...(current[sourceId] ?? {
          name: '',
          url: '',
          monitorMode: 'manual',
          intervalSeconds: '300',
          jitterPercent: '20',
          stopAfterVintedSessionUses: '',
          windowStart: '',
          windowEnd: '',
          sessionDurationMinutes: '60',
          filterTerms: ''
        }),
        [field]: value
      }
    }));
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
    try {
      const generation = nextRequestGeneration(monitorRunsRequestGenerationRef.current, sourceId);
      const sourceRuns = await fetchRuns({ source_id: sourceId, limit });
      if (monitorRunsRequestGenerationRef.current.get(sourceId) === generation) {
        setMonitorRunsBySource((current) => ({ ...current, [sourceId]: sourceRuns }));
        setPendingStopSourceIds((current) => current.filter((pendingSourceId) => pendingSourceId !== sourceId));
        setError((current) => (
          current === MONITOR_RUN_STATE_LOAD_ERROR || current === STOP_MONITOR_STATE_REFRESH_ERROR ? null : current
        ));
      }
    } catch (caught) {
      setError(MONITOR_RUN_STATE_LOAD_ERROR);
      throw caught;
    }
  }

  const loadMonitorEvents = useCallback(async (sourceId: number) => {
    const generation = nextRequestGeneration(monitorEventsRequestGenerationRef.current, sourceId);
    try {
      const events = await fetchMonitorEvents(sourceId);
      if (monitorEventsRequestGenerationRef.current.get(sourceId) !== generation) {
        return;
      }
      setMonitorEventsBySource((current) => ({
        ...current,
        [sourceId]: mergeRunEvents(events, current[sourceId] ?? [])
      }));
      setMonitorEventHistoryLoadedBySource((current) => ({ ...current, [sourceId]: true }));
    } catch (caught) {
      if (monitorEventsRequestGenerationRef.current.get(sourceId) === generation) {
        setError(caught instanceof Error ? caught.message : 'No se pudieron cargar los logs acumulados');
      }
    }
  }, []);

  const appendMonitorEvent = useCallback((event: RunEvent) => {
    const sourceId = event.source_id;
    if (!sourceId) {
      return;
    }
    if (!monitorStreamRuntimeRef.current.sourceIds.has(sourceId)) {
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
    if (section === activeSection) {
      return;
    }
    if (activeSection === 'sources' && editingSourceId !== null) {
      if (monitorCommandRef.current?.kind === 'save') {
        setError('Espera a que termine el guardado antes de salir de Monitores');
        return;
      }
      if (sourceEditHasChanges()) {
        setPendingSourceNavigation({ kind: 'section', section });
        return;
      }
      resetSourceEditDraft();
      setEditingSourceId(null);
      setPendingSourceNavigation(null);
    }
    activateSection(section);
  }

  function activateSection(section: string) {
    setActiveSection(section);
    window.setTimeout(() => window.scrollTo({ top: 0, left: 0 }), 0);
  }

  return {
    activeSection,
    activeSubtitle,
    activeTitle,
    changeResultsPageSize,
    clearOpportunityFilters,
    creatingSource: monitorCommand?.kind === 'create',
    editingSourceId,
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
    onBeginSourceEdit: beginSourceEdit,
    onCancelSourceEdit: cancelSourceEdit,
    onConfirmDiscardSourceEdit: confirmDiscardSourceEdit,
    onKeepSourceEditing: keepSourceEditing,
    onSelectMonitor: requestMonitorSelection,
    onSaveSourceSchedule,
    onRunNow,
    onStartSession,
    onStopMonitor,
    onTestProxy,
    onToggleProxy,
    onUpdateSchedulerConfig,
    monitorStatsBySource,
    monitorStatsRangeBySource,
    monitorRunsBySource,
    monitorEventsBySource,
    monitorEventHistoryLoadedBySource,
    monitorHiddenEventIdsBySource,
    monitorCommandPending,
    monitorStreamStatus,
    monitorStreamReady,
    opportunityCollectionState,
    opportunityPage,
    pendingStopSourceIds,
    pendingSourceNavigation,
    proxyDraft,
    proxyCollectionState,
    proxyProfiles,
    proxyActionMessages,
    refreshRuntime,
    opportunityFilters,
    opportunitiesPageSize,
    runningSessionId,
    requestedSelectedMonitorId,
    savingProxy,
    savingSourceId,
    scheduler,
    schedulerAvailabilityError,
    selectSection,
    setNavCollapsed,
    setProxyDraft,
    setSourceName,
    setSourceUrl,
    sourceDrafts,
    sourceCollectionState,
    sourceName,
    sources,
    sourceUrl,
    testingProxyIds,
    updateOpportunityFilter,
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
      name: draft.name,
      url: draft.url,
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

function markPreparedSessionsExpired(sourceData: SearchSource[], expiryThreshold: number): SearchSource[] {
  return sourceData.map((source) => {
    let changed = false;
    const preparedSessions = source.prepared_sessions.map((session) => {
      const expiresAt = session.expires_at ? Date.parse(session.expires_at) : Number.NaN;
      if (!session.usable_now || !Number.isFinite(expiresAt) || expiresAt > expiryThreshold) {
        return session;
      }
      changed = true;
      return { ...session, usable_now: false, unusable_reason: 'expired' as const };
    });
    return changed ? { ...source, prepared_sessions: preparedSessions } : source;
  });
}

function addId(current: number[], id: number): number[] {
  return current.includes(id) ? current : [...current, id];
}

function hasNonTerminalSessionRun(runs: Run[]): boolean {
  return runs.some(
    (run) => run.monitor_session_id !== null && (run.status === 'running' || run.status === 'finalizing')
  );
}

function mergeMissingSourceDrafts(
  current: Record<number, SourceDraft>,
  sources: SearchSource[]
): Record<number, SourceDraft> {
  const missingSources = sources.filter((source) => !Object.hasOwn(current, source.id));
  return missingSources.length === 0 ? current : { ...current, ...buildSourceDrafts(missingSources) };
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

function sectionSubtitle(
  section: string,
  opportunityTotal: number,
  sourceTotal: number,
  opportunityState: CollectionLoadState,
  sourceState: CollectionLoadState
): string {
  if (section === 'opportunities') {
    if (opportunityState === 'loading') {
      return 'Cargando oportunidades';
    }
    if (opportunityState === 'unavailable') {
      return 'Oportunidades no disponibles';
    }
    return `${opportunityTotal} oportunidades`;
  }
  if (section === 'sources') {
    if (sourceState === 'loading') {
      return 'Cargando monitores';
    }
    if (sourceState === 'unavailable') {
      return 'Monitores no disponibles';
    }
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

function withoutSource<T>(current: Record<number, T>, sourceId: number): Record<number, T> {
  if (!Object.hasOwn(current, sourceId)) {
    return current;
  }
  const next = { ...current };
  delete next[sourceId];
  return next;
}
