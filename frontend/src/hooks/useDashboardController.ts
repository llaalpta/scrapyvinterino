import { type FormEvent, useEffect, useMemo, useState } from 'react';
import {
  createFilterRule,
  createProxyProfile,
  createSource,
  deleteSource,
  fetchFilterRules,
  fetchOpportunities,
  fetchProxyProfiles,
  fetchRunEvents,
  fetchRuns,
  fetchScheduler,
  fetchSources,
  runMonitor,
  startMonitor,
  stopMonitor,
  testProxyProfile,
  updateScheduler,
  updateSource,
  type FilterRule,
  type OpportunityResult,
  type Page,
  type ProxyProfile,
  type Run,
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
import { buildSourceDraft, buildSourceDrafts, type SourceDraft } from '../features/sources/sourceDrafts';

const emptyOpportunityPage: Page<OpportunityResult> = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0 };
const emptyProxyDraft: ProxyDraft = { name: '', scheme: 'http', host: '', port: '', username: '', password: '' };

export function useDashboardController() {
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [filterRules, setFilterRules] = useState<FilterRule[]>([]);
  const [proxyProfiles, setProxyProfiles] = useState<ProxyProfile[]>([]);
  const [opportunityPage, setOpportunityPage] = useState<Page<OpportunityResult>>(emptyOpportunityPage);
  const [runs, setRuns] = useState<Run[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerState | null>(null);
  const [sourceDrafts, setSourceDrafts] = useState<Record<number, SourceDraft>>({});
  const [selectedFilterIdsBySource, setSelectedFilterIdsBySource] = useState<Record<number, number[]>>({});
  const [selectedProxyBySource, setSelectedProxyBySource] = useState<Record<number, string>>({});
  const [opportunityFilters, setOpportunityFilters] = useState<OpportunityFilters>(defaultOpportunityFilters);
  const [opportunitiesPageSize, setOpportunitiesPageSize] = useState(25);
  const [runningSessionId, setRunningSessionId] = useState<number | null>(null);
  const [savingSourceId, setSavingSourceId] = useState<number | null>(null);
  const [savingScheduler, setSavingScheduler] = useState(false);
  const [savingFilter, setSavingFilter] = useState(false);
  const [savingProxy, setSavingProxy] = useState(false);
  const [loadingOpportunities, setLoadingOpportunities] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [filterName, setFilterName] = useState('');
  const [filterTerms, setFilterTerms] = useState('');
  const [proxyDraft, setProxyDraft] = useState<ProxyDraft>(emptyProxyDraft);
  const [activeSection, setActiveSection] = useState('opportunities');
  const [navCollapsed, setNavCollapsed] = useState(false);
  const activeTitle = useMemo(() => navItems.find((item) => item.id === activeSection)?.label ?? 'Oportunidades', [activeSection]);
  const activeSubtitle = useMemo(
    () => sectionSubtitle(activeSection, opportunityPage.total, sources.length),
    [activeSection, opportunityPage.total, sources.length]
  );

  useEffect(() => {
    Promise.all([
      fetchSources(),
      fetchOpportunities(),
      fetchRuns(),
      fetchScheduler(),
      fetchFilterRules(),
      fetchProxyProfiles()
    ])
      .then(([sourceData, opportunityData, runData, schedulerData, filterData, proxyData]) => {
        setSources(sourceData);
        setOpportunityPage(opportunityData);
        setRuns(runData);
        setScheduler(schedulerData);
        setFilterRules(filterData);
        setProxyProfiles(proxyData);
        setSourceDrafts(buildSourceDrafts(sourceData));
        setSelectedFilterIdsBySource(buildSelectedFilterIds(sourceData));
        setSelectedProxyBySource(buildSelectedProxyIds(sourceData));
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : 'Error cargando datos');
      });
  }, []);

  async function refreshRuntime() {
    const [opportunityData, runData] = await Promise.all([fetchOpportunities(), fetchRuns()]);
    setOpportunityPage(opportunityData);
    setRuns(runData);
  }

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
      setSourceName('');
      setSourceUrl('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo crear el monitor');
    }
  }

  async function onCreateFilter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSavingFilter(true);
    try {
      const created = await createFilterRule({
        name: filterName,
        definition: { blacklist_terms: filterTerms.split(',').map((entry) => entry.trim()).filter(Boolean) }
      });
      setFilterRules((current) => [created, ...current]);
      setFilterName('');
      setFilterTerms('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo guardar el filtro');
    } finally {
      setSavingFilter(false);
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
        host: proxyDraft.host,
        port: Number(proxyDraft.port),
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
    try {
      const updated = await testProxyProfile(profileId);
      setProxyProfiles((current) => current.map((profile) => (profile.id === updated.id ? updated : profile)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo probar el proxy');
    }
  }

  async function onRunMonitor(sourceId: number) {
    setError(null);
    setRunningSessionId(sourceId);
    try {
      const created = await runMonitor(sourceId);
      const [sourceData, opportunityData, runData] = await Promise.all([
        fetchSources(),
        fetchOpportunities(buildOpportunityQuery(opportunityFilters, 1, opportunitiesPageSize)),
        fetchRuns()
      ]);
      setSources(sourceData);
      setSourceDrafts(buildSourceDrafts(sourceData));
      setRuns([created, ...runData.filter((run) => run.id !== created.id)].slice(0, 50));
      setOpportunityPage(opportunityData);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo ejecutar el monitor');
    } finally {
      setRunningSessionId(null);
    }
  }

  async function onStartSession(source: SearchSource) {
    const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
    setError(null);
    setRunningSessionId(source.id);
    try {
      await saveMonitorConfig(source, draft);
      const run = draft.monitorMode === 'manual' ? await runMonitor(source.id) : await startMonitor(source.id);
      const [sourceData, opportunityData, runData] = await Promise.all([
        fetchSources(),
        fetchOpportunities(buildOpportunityQuery(opportunityFilters, 1, opportunitiesPageSize)),
        fetchRuns()
      ]);
      setSources(sourceData);
      setSourceDrafts(buildSourceDrafts(sourceData));
      setRuns([run, ...runData.filter((entry) => entry.id !== run.id)].slice(0, 50));
      setOpportunityPage(opportunityData);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo lanzar el monitor');
    } finally {
      setRunningSessionId(null);
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

  async function onToggleSource(source: SearchSource) {
    setError(null);
    setSavingSourceId(source.id);
    try {
      replaceSource(await updateSource(source.id, { is_active: !source.is_active }));
      await refreshRuntime();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo actualizar el monitor');
    } finally {
      setSavingSourceId(null);
    }
  }

  async function onDeleteSource(source: SearchSource) {
    setError(null);
    setSavingSourceId(source.id);
    try {
      await deleteSource(source.id);
      setSources((current) => current.filter((entry) => entry.id !== source.id));
      setSourceDrafts((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      setSelectedFilterIdsBySource((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      setSelectedProxyBySource((current) => {
        const next = { ...current };
        delete next[source.id];
        return next;
      });
      await refreshRuntime();
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
      setError(caught instanceof Error ? caught.message : 'No se pudo guardar la cadencia');
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
          sessionDurationMinutes: '60'
        }),
        [field]: value
      }
    }));
  }

  function updateSourceProxy(sourceId: number, value: string) {
    setSelectedProxyBySource((current) => ({ ...current, [sourceId]: value }));
  }

  function toggleSourceFilter(sourceId: number, filterId: number) {
    setSelectedFilterIdsBySource((current) => {
      const selected = current[sourceId] ?? [];
      const next = selected.includes(filterId) ? selected.filter((entry) => entry !== filterId) : [...selected, filterId];
      return { ...current, [sourceId]: next };
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
    error,
    filterName,
    filterRules,
    filterTerms,
    getSourceName,
    loadOpportunities,
    loadingOpportunities,
    navCollapsed,
    onCreateFilter,
    onCreateProxy,
    onCreateSource,
    onDeleteSource,
    onLoadRunEvents: fetchRunEvents,
    onRunMonitor,
    onSaveSourceSchedule,
    onStartSession,
    onStopMonitor,
    onTestProxy,
    onToggleScheduler,
    onToggleSource,
    opportunityPage,
    proxyDraft,
    proxyProfiles,
    refreshRuntime,
    opportunityFilters,
    opportunitiesPageSize,
    runningSessionId,
    savingFilter,
    savingProxy,
    savingScheduler,
    savingSourceId,
    scheduler,
    selectSection,
    selectedFilterIdsBySource,
    selectedProxyBySource,
    setFilterName,
    setFilterTerms,
    setNavCollapsed,
    setProxyDraft,
    setSourceName,
    setSourceUrl,
    sourceDrafts,
    sourceName,
    sources,
    sourceUrl,
    runs,
    toggleSourceFilter,
    updateOpportunityFilter,
    updateSourceDraft,
    updateSourceProxy
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
    const durationMinutes =
      draft.monitorMode === 'duration' ? parseIntegerInRange(draft.sessionDurationMinutes, 'La duracion del monitor', 1, 1440) : null;
    return updateSource(source.id, {
      monitor_mode: draft.monitorMode,
      duration_minutes: durationMinutes,
      filter_rule_ids: selectedFilterIdsBySource[source.id] ?? [],
      proxy_profile_id: selectedProxyBySource[source.id] ? Number(selectedProxyBySource[source.id]) : null,
      scheduler_config: {
        interval_seconds: intervalSeconds,
        jitter_percent: jitterPercent,
        allowed_windows: draft.monitorMode === 'window' ? allowedWindows : []
      }
    });
  }
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
  if (section === 'filters') {
    return 'Filtros opcionales para oportunidades';
  }
  return `${opportunityTotal} oportunidades`;
}

function buildSelectedFilterIds(sources: SearchSource[]): Record<number, number[]> {
  return Object.fromEntries(sources.map((source) => [source.id, source.filter_rule_ids ?? []]));
}

function buildSelectedProxyIds(sources: SearchSource[]): Record<number, string> {
  return Object.fromEntries(
    sources.flatMap((source) => (source.proxy_profile_id ? [[source.id, String(source.proxy_profile_id)]] : []))
  );
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
