import { type FormEvent, useEffect, useMemo, useState } from 'react';
import {
  createFilterRule,
  createProxyProfile,
  createSource,
  fetchFilterRules,
  fetchItems,
  fetchMonitorSessions,
  fetchOpportunities,
  fetchProxyProfiles,
  fetchRunEvents,
  fetchRuns,
  fetchScheduler,
  fetchSources,
  runMonitorSession,
  startMonitorSession,
  stopMonitorSession,
  testProxyProfile,
  updateScheduler,
  updateSource,
  type FilterRule,
  type ItemResult,
  type MonitorSession,
  type OpportunityResult,
  type Page,
  type ProxyProfile,
  type Run,
  type RunEvent,
  type SchedulerState,
  type SearchSource
} from '../api';
import { navItems } from '../app/navigation';
import { type ProxyDraft } from '../features/settings/SettingsView';
import { buildItemQuery, defaultFilters, type ResultFilters } from '../features/results/resultFilters';
import { buildSourceDraft, buildSourceDrafts, type SourceDraft } from '../features/sources/sourceDrafts';

const emptyItemPage: Page<ItemResult> = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0 };
const emptyOpportunityPage: Page<OpportunityResult> = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0 };
const emptyProxyDraft: ProxyDraft = { name: '', scheme: 'http', host: '', port: '', username: '', password: '' };

export function useDashboardController() {
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [filterRules, setFilterRules] = useState<FilterRule[]>([]);
  const [proxyProfiles, setProxyProfiles] = useState<ProxyProfile[]>([]);
  const [monitorSessions, setMonitorSessions] = useState<MonitorSession[]>([]);
  const [itemPage, setItemPage] = useState<Page<ItemResult>>(emptyItemPage);
  const [opportunityPage, setOpportunityPage] = useState<Page<OpportunityResult>>(emptyOpportunityPage);
  const [runs, setRuns] = useState<Run[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerState | null>(null);
  const [sourceDrafts, setSourceDrafts] = useState<Record<number, SourceDraft>>({});
  const [selectedFilterIdsBySource, setSelectedFilterIdsBySource] = useState<Record<number, number[]>>({});
  const [selectedProxyBySource, setSelectedProxyBySource] = useState<Record<number, string>>({});
  const [resultFilters, setResultFilters] = useState<ResultFilters>(defaultFilters);
  const [resultsPageSize, setResultsPageSize] = useState(25);
  const [runningSessionId, setRunningSessionId] = useState<number | null>(null);
  const [savingSourceId, setSavingSourceId] = useState<number | null>(null);
  const [savingScheduler, setSavingScheduler] = useState(false);
  const [savingFilter, setSavingFilter] = useState(false);
  const [savingProxy, setSavingProxy] = useState(false);
  const [loadingResults, setLoadingResults] = useState(false);
  const [loadingOpportunities, setLoadingOpportunities] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [filterName, setFilterName] = useState('');
  const [filterTerms, setFilterTerms] = useState('');
  const [proxyDraft, setProxyDraft] = useState<ProxyDraft>(emptyProxyDraft);
  const [activeSection, setActiveSection] = useState('results');
  const [navCollapsed, setNavCollapsed] = useState(false);
  const activeSession = monitorSessions.find((session) => session.status === 'active');
  const activeTitle = useMemo(() => navItems.find((item) => item.id === activeSection)?.label ?? 'Resultados', [activeSection]);
  const activeSubtitle = useMemo(
    () => sectionSubtitle(activeSection, itemPage.total, opportunityPage.total, sources.length, runs.length, monitorSessions.length),
    [activeSection, itemPage.total, opportunityPage.total, sources.length, runs.length, monitorSessions.length]
  );

  useEffect(() => {
    Promise.all([
      fetchSources(),
      fetchItems(),
      fetchOpportunities(),
      fetchRuns(),
      fetchScheduler(),
      fetchFilterRules(),
      fetchProxyProfiles(),
      fetchMonitorSessions()
    ])
      .then(([sourceData, itemData, opportunityData, runData, schedulerData, filterData, proxyData, sessionData]) => {
        setSources(sourceData);
        setItemPage(itemData);
        setOpportunityPage(opportunityData);
        setRuns(runData);
        setScheduler(schedulerData);
        setFilterRules(filterData);
        setProxyProfiles(proxyData);
        setMonitorSessions(sessionData);
        setSourceDrafts(buildSourceDrafts(sourceData));
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : 'Error cargando datos');
      });
  }, []);

  async function refreshRuntime() {
    const [opportunityData, runData, sessionData] = await Promise.all([fetchOpportunities(), fetchRuns(), fetchMonitorSessions()]);
    setOpportunityPage(opportunityData);
    setRuns(runData);
    setMonitorSessions(sessionData);
  }

  async function loadItems(page = 1, filters = resultFilters, pageSize = resultsPageSize) {
    setLoadingResults(true);
    setError(null);
    try {
      setItemPage(await fetchItems(buildItemQuery(filters, page, pageSize)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudieron cargar los resultados');
    } finally {
      setLoadingResults(false);
    }
  }

  async function loadOpportunities(page = 1) {
    setLoadingOpportunities(true);
    setError(null);
    try {
      setOpportunityPage(await fetchOpportunities({ page, page_size: opportunityPage.page_size }));
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
      setError(caught instanceof Error ? caught.message : 'No se pudo crear la fuente');
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

  async function onRunSession(sessionId: number) {
    setError(null);
    setRunningSessionId(sessionId);
    try {
      const created = await runMonitorSession(sessionId);
      const [itemData, opportunityData, runData, sessionData] = await Promise.all([
        fetchItems(buildItemQuery(resultFilters, 1, resultsPageSize)),
        fetchOpportunities(),
        fetchRuns(),
        fetchMonitorSessions()
      ]);
      setRuns([created, ...runData.filter((run) => run.id !== created.id)].slice(0, 50));
      setItemPage(itemData);
      setOpportunityPage(opportunityData);
      setMonitorSessions(sessionData);
      setActiveSection('runs');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo ejecutar la sesion');
    } finally {
      setRunningSessionId(null);
    }
  }

  async function onStartSession(source: SearchSource) {
    setError(null);
    try {
      const proxyValue = selectedProxyBySource[source.id];
      const created = await startMonitorSession({
        source_id: source.id,
        filter_rule_ids: selectedFilterIdsBySource[source.id] ?? [],
        proxy_profile_id: proxyValue ? Number(proxyValue) : null
      });
      setMonitorSessions((current) => [created, ...current]);
      setActiveSection('runs');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo lanzar la sesion');
    }
  }

  async function onStopSession(sessionId: number) {
    setError(null);
    try {
      const stopped = await stopMonitorSession(sessionId);
      setMonitorSessions((current) => current.map((session) => (session.id === stopped.id ? stopped : session)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo detener la sesion');
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
      setError(caught instanceof Error ? caught.message : 'No se pudo actualizar la fuente');
    } finally {
      setSavingSourceId(null);
    }
  }

  async function onSaveSourceSchedule(source: SearchSource) {
    const draft = sourceDrafts[source.id] ?? buildSourceDraft(source);
    setError(null);
    setSavingSourceId(source.id);
    try {
      const updated = await updateSource(source.id, {
        scheduler_config: {
          interval_seconds: Number(draft.intervalSeconds),
          jitter_percent: Number(draft.jitterPercent),
          allowed_windows: draft.allowedWindows
            .split(',')
            .map((entry) => entry.trim())
            .filter(Boolean)
        }
      });
      replaceSource(updated);
      setSourceDrafts((current) => ({ ...current, [updated.id]: buildSourceDraft(updated) }));
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
        ...(current[sourceId] ?? { intervalSeconds: '300', jitterPercent: '20', allowedWindows: '' }),
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

  function updateResultFilter(field: keyof ResultFilters, value: string) {
    setResultFilters((current) => ({ ...current, [field]: value }));
  }

  function clearResultFilters() {
    setResultFilters(defaultFilters);
    void loadItems(1, defaultFilters, resultsPageSize);
  }

  function changeResultsPageSize(pageSize: number) {
    setResultsPageSize(pageSize);
    void loadItems(1, resultFilters, pageSize);
  }

  function getSourceName(sourceId: number): string {
    return sources.find((source) => source.id === sourceId)?.name ?? `Fuente ${sourceId}`;
  }

  function selectSection(section: string) {
    setActiveSection(section);
    window.setTimeout(() => window.scrollTo({ top: 0, left: 0 }), 0);
  }

  return {
    activeSection,
    activeSession,
    activeSubtitle,
    activeTitle,
    changeResultsPageSize,
    clearResultFilters,
    error,
    filterName,
    filterRules,
    filterTerms,
    getSourceName,
    itemPage,
    loadItems,
    loadOpportunities,
    loadingOpportunities,
    loadingResults,
    monitorSessions,
    navCollapsed,
    onCreateFilter,
    onCreateProxy,
    onCreateSource,
    onLoadRunEvents: fetchRunEvents,
    onRunSession,
    onSaveSourceSchedule,
    onStartSession,
    onStopSession,
    onTestProxy,
    onToggleScheduler,
    onToggleSource,
    opportunityPage,
    proxyDraft,
    proxyProfiles,
    resultFilters,
    resultsPageSize,
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
    updateResultFilter,
    updateSourceDraft,
    updateSourceProxy
  };
}

function sectionSubtitle(
  section: string,
  itemTotal: number,
  opportunityTotal: number,
  sourceTotal: number,
  runTotal: number,
  sessionTotal: number
): string {
  if (section === 'opportunities') {
    return `${opportunityTotal} oportunidades`;
  }
  if (section === 'sources') {
    return `${sourceTotal} fuentes configuradas - ${sessionTotal} sesiones`;
  }
  if (section === 'runs') {
    return `${runTotal} ejecuciones registradas`;
  }
  if (section === 'settings') {
    return 'Configuracion local del monitor';
  }
  if (section === 'filters') {
    return 'Blacklists excluyentes para sesiones';
  }
  return `${itemTotal} resultados para la consulta actual`;
}
