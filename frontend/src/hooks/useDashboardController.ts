import { type FormEvent, useEffect, useMemo, useState } from 'react';
import {
  createSource,
  fetchItems,
  fetchOpportunities,
  fetchRuns,
  fetchScheduler,
  fetchSources,
  runSource,
  updateScheduler,
  updateSource,
  type ItemResult,
  type OpportunityResult,
  type Page,
  type Run,
  type SchedulerState,
  type SearchSource
} from '../api';
import { buildItemQuery, defaultFilters, type ResultFilters } from '../features/results/resultFilters';
import { buildSourceDraft, buildSourceDrafts, type SourceDraft } from '../features/sources/sourceDrafts';
import { navItems } from '../app/navigation';

const emptyItemPage: Page<ItemResult> = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0 };
const emptyOpportunityPage: Page<OpportunityResult> = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0 };

export function useDashboardController() {
  const [sources, setSources] = useState<SearchSource[]>([]);
  const [itemPage, setItemPage] = useState<Page<ItemResult>>(emptyItemPage);
  const [opportunityPage, setOpportunityPage] = useState<Page<OpportunityResult>>(emptyOpportunityPage);
  const [runs, setRuns] = useState<Run[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerState | null>(null);
  const [sourceDrafts, setSourceDrafts] = useState<Record<number, SourceDraft>>({});
  const [resultFilters, setResultFilters] = useState<ResultFilters>(defaultFilters);
  const [resultsPageSize, setResultsPageSize] = useState(25);
  const [runningSourceId, setRunningSourceId] = useState<number | null>(null);
  const [savingSourceId, setSavingSourceId] = useState<number | null>(null);
  const [savingScheduler, setSavingScheduler] = useState(false);
  const [loadingResults, setLoadingResults] = useState(false);
  const [loadingOpportunities, setLoadingOpportunities] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [activeSection, setActiveSection] = useState('results');
  const [navCollapsed, setNavCollapsed] = useState(false);
  const activeSource = sources.find((source) => source.is_active);
  const activeTitle = useMemo(() => navItems.find((item) => item.id === activeSection)?.label ?? 'Resultados', [activeSection]);
  const activeSubtitle = useMemo(
    () => sectionSubtitle(activeSection, itemPage.total, opportunityPage.total, sources.length, runs.length),
    [activeSection, itemPage.total, opportunityPage.total, sources.length, runs.length]
  );

  useEffect(() => {
    Promise.all([fetchSources(), fetchItems(), fetchOpportunities(), fetchRuns(), fetchScheduler()])
      .then(([sourceData, itemData, opportunityData, runData, schedulerData]) => {
        setSources(sourceData);
        setItemPage(itemData);
        setOpportunityPage(opportunityData);
        setRuns(runData);
        setScheduler(schedulerData);
        setSourceDrafts(buildSourceDrafts(sourceData));
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : 'Error cargando datos');
      });
  }, []);

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

  async function onRunSource(sourceId: number) {
    setError(null);
    setRunningSourceId(sourceId);
    try {
      const created = await runSource(sourceId);
      const [itemData, runData] = await Promise.all([fetchItems(buildItemQuery(resultFilters, 1, resultsPageSize)), fetchRuns()]);
      setRuns([created, ...runData.filter((run) => run.id !== created.id)].slice(0, 50));
      setItemPage(itemData);
      setActiveSection('runs');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'No se pudo ejecutar la busqueda');
    } finally {
      setRunningSourceId(null);
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
    activeSource,
    activeSubtitle,
    activeTitle,
    changeResultsPageSize,
    clearResultFilters,
    error,
    getSourceName,
    itemPage,
    loadItems,
    loadOpportunities,
    loadingOpportunities,
    loadingResults,
    navCollapsed,
    onCreateSource,
    onRunSource,
    onSaveSourceSchedule,
    onToggleScheduler,
    onToggleSource,
    opportunityPage,
    resultFilters,
    resultsPageSize,
    runningSourceId,
    savingScheduler,
    savingSourceId,
    scheduler,
    selectSection,
    setNavCollapsed,
    setSourceName,
    setSourceUrl,
    sourceDrafts,
    sourceName,
    sources,
    sourceUrl,
    runs,
    updateResultFilter,
    updateSourceDraft
  };
}

function sectionSubtitle(section: string, itemTotal: number, opportunityTotal: number, sourceTotal: number, runTotal: number): string {
  if (section === 'opportunities') {
    return `${opportunityTotal} oportunidades`;
  }
  if (section === 'sources') {
    return `${sourceTotal} fuentes configuradas`;
  }
  if (section === 'runs') {
    return `${runTotal} ejecuciones registradas`;
  }
  if (section === 'settings') {
    return 'Configuracion local del monitor';
  }
  if (section === 'filters') {
    return 'Reglas locales pendientes de implementar';
  }
  return `${itemTotal} resultados para la consulta actual`;
}
