import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import {
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
  prepareMonitorVintedSession,
  probeMonitorItemDetail,
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

export function useDashboardController() {
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [proxyProfiles, setProxyProfiles] = useState<ProxyProfile[]>([]);
  const [opportunityPage, setOpportunityPage] = useState<Page<OpportunityResult>>(emptyOpportunityPage);
  const [runs, setRuns] = useState<Run[]>([]);
  const [monitorRunsBySource, setMonitorRunsBySource] = useState<Record<number, Run[]>>({});
  const [monitorEventsBySource, setMonitorEventsBySource] = useState<Record<number, RunEvent[]>>({});
  const [monitorHiddenEventIdsBySource, setMonitorHiddenEventIdsBySource] = useState<Record<number, number[]>>({});
  const [monitorStatsBySource, setMonitorStatsBySource] = useState<Record<number, MonitorStats>>({});
  const [monitorStatsRangeBySource, setMonitorStatsRangeBySource] = useState<Record<number, MonitorStatsRange>>({});
  const [detailProbeRefs, setDetailProbeRefs] = useState<Record<number, string>>({});
  const [detailProbeMessages, setDetailProbeMessages] = useState<Record<number, string>>({});
  const [scheduler, setScheduler] = useState<SchedulerState | null>(null);
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
  const activeTitle = useMemo(() => navItems.find((item) => item.id === activeSection)?.label ?? 'Oportunidades', [activeSection]);
  const activeSubtitle = useMemo(
    () => sectionSubtitle(activeSection, opportunityPage.total, sources.length),
    [activeSection, opportunityPage.total, sources.length]
  );

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
    Promise.all([
      fetchSources(),
      fetchOpportunities(),
      fetchRuns(),
      fetchScheduler(),
      fetchProxyProfiles()
    ])
      .then(([sourceData, opportunityData, runData, schedulerData, proxyData]) => {
        setSources(sourceData);
        setOpportunityPage(opportunityData);
        setRuns(runData);
        setScheduler(schedulerData);
        setProxyProfiles(proxyData);
        setSourceDrafts(buildSourceDrafts(sourceData));
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : 'Error cargando datos');
      });
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
    } catch (caught) {
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
    } catch (caught) {
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
    const stats = await fetchMonitorStats(sourceId, range);
    setMonitorStatsBySource((current) => ({ ...current, [sourceId]: stats }));
  }

  async function loadMonitorRuns(sourceId: number, limit = MONITOR_RUN_HISTORY_LIMIT) {
    const sourceRuns = await fetchRuns({ source_id: sourceId, limit });
    setMonitorRunsBySource((current) => ({ ...current, [sourceId]: sourceRuns }));
  }

  const loadMonitorEvents = useCallback(async (sourceId: number) => {
    try {
      const events = await fetchMonitorEvents(sourceId);
      setMonitorEventsBySource((current) => ({ ...current, [sourceId]: events }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudieron cargar los logs acumulados');
    }
  }, []);

  const appendMonitorEvent = useCallback((event: RunEvent) => {
    const sourceId = event.source_id;
    if (!sourceId) {
      return;
    }
    setMonitorEventsBySource((current) => {
      const existing = current[sourceId];
      if (!existing || existing.some((entry) => entry.id === event.id)) {
        return current;
      }
      return {
        ...current,
        [sourceId]: [...existing, event].sort((left, right) => left.id - right.id)
      };
    });
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
    monitorHiddenEventIdsBySource,
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
  const matrixDuration = typeof result.matrix_duration_ms === 'number' ? `total_ms=${result.matrix_duration_ms}` : null;
  const attemptCount = typeof result.attempt_count === 'number' ? `intentos=${result.attempt_count}` : null;
  const controlOutcome = typeof result.control_outcome === 'string' ? `control=${result.control_outcome}` : null;
  const summary = recordValue(result.detail_summary);
  const photos = numberOrString(summary?.photo_count);
  const description = numberOrString(summary?.description_length);
  const seller = summary?.seller_present === true ? 'seller=ok' : summary?.seller_present === false ? 'seller=missing' : null;
  const title = summary?.title_present === true ? 'title=ok' : summary?.title_present === false ? 'title=missing' : null;
  const tokens = [
    status,
    duration,
    matrixDuration,
    attemptCount,
    controlOutcome,
    title,
    photos ? `photos=${photos}` : null,
    description ? `description_chars=${description}` : null,
    seller
  ]
    .filter(Boolean)
    .join(' ');
  const attempts = Array.isArray(result.attempts)
    ? result.attempts.map((entry) => recordValue(entry)).filter((entry): entry is Record<string, unknown> => entry !== null)
    : [];
  const matrix = attempts
    .map((attempt) => {
      const endpoint = typeof attempt?.endpoint === 'string' ? attempt.endpoint : 'endpoint';
      const referer = typeof attempt?.referer_mode === 'string' ? attempt.referer_mode : 'referer';
      const attemptOutcome = typeof attempt?.outcome === 'string' ? attempt.outcome : 'unknown';
      const attemptStatus = typeof attempt?.status_code === 'number' ? `status=${attempt.status_code}` : 'status=-';
      const attemptMs = typeof attempt?.duration_ms === 'number' ? `ms=${attempt.duration_ms}` : null;
      const extras = [
        attempt?.pre_navigation === true ? 'pre-nav' : null,
        attempt?.client_hints === true ? 'client-hints' : null,
        typeof attempt?.cf_mitigated === 'string' ? `cf=${attempt.cf_mitigated}` : null
      ]
        .filter(Boolean)
        .join(' ');
      return `- ${endpoint}/${referer}: ${attemptOutcome} ${[attemptStatus, attemptMs, extras].filter(Boolean).join(' ')}`;
    })
    .join('\n');
  const firstLine = tokens ? `Detalle ${outcome}: ${tokens}` : `Detalle ${outcome}`;
  return matrix ? `${firstLine}\n${matrix}` : firstLine;
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
