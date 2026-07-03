import {
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Heart,
  Play,
  Power,
  RotateCcw,
  Save,
  Search,
  SlidersHorizontal,
  X,
  ShoppingCart
} from 'lucide-react';
import { FormEvent, useEffect, useMemo, useState } from 'react';
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
  type Item,
  type ItemQuery,
  type ItemResult,
  type OpportunityResult,
  type Page,
  type Run,
  type SchedulerState,
  type SearchSource
} from './api';

const navItems = [
  { id: 'results', label: 'Resultados' },
  { id: 'opportunities', label: 'Oportunidades' },
  { id: 'sources', label: 'Fuentes' },
  { id: 'filters', label: 'Filtros' },
  { id: 'runs', label: 'Runs' },
  { id: 'settings', label: 'Settings' }
];

const emptyItemPage: Page<ItemResult> = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0 };
const emptyOpportunityPage: Page<OpportunityResult> = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0 };

type SourceDraft = {
  intervalSeconds: string;
  jitterPercent: string;
  allowedWindows: string;
};

type ResultFilters = {
  sourceId: string;
  scrapedFrom: string;
  scrapedTo: string;
  priceMin: string;
  priceMax: string;
};

const defaultFilters: ResultFilters = {
  sourceId: '',
  scrapedFrom: '',
  scrapedTo: '',
  priceMin: '',
  priceMax: ''
};

export function App() {
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

  return (
    <main className="shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">Personal dashboard</p>
          <h1>Vinted Monitor</h1>
        </div>
        <nav>
          {navItems.map((item) => (
            <button
              className={activeSection === item.id ? 'active' : ''}
              key={item.id}
              type="button"
              onClick={() => setActiveSection(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <h2>{activeTitle}</h2>
            <p>{activeSubtitle}</p>
          </div>
          <button
            type="button"
            disabled={!activeSource || runningSourceId !== null}
            title={activeSource ? 'Ejecutar fuente activa' : 'Crea una fuente activa para ejecutar una busqueda'}
            onClick={() => {
              if (activeSource) {
                void onRunSource(activeSource.id);
              }
            }}
          >
            <Play size={18} />
            {runningSourceId ? 'Ejecutando...' : 'Ejecutar busqueda'}
          </button>
        </header>

        {error ? <div className="notice">{error}</div> : null}

        {activeSection === 'results' ? (
          <ResultsView
            filters={resultFilters}
            itemPage={itemPage}
            loading={loadingResults}
            pageSize={resultsPageSize}
            sources={sources}
            onApply={() => void loadItems(1)}
            onClear={clearResultFilters}
            onFilterChange={updateResultFilter}
            onPageChange={(page) => void loadItems(page)}
            onPageSizeChange={changeResultsPageSize}
          />
        ) : null}

        {activeSection === 'opportunities' ? (
          <OpportunitiesView
            loading={loadingOpportunities}
            opportunityPage={opportunityPage}
            onPageChange={(page) => void loadOpportunities(page)}
          />
        ) : null}

        {activeSection === 'sources' ? (
          <SourcesView
            onCreateSource={onCreateSource}
            onRunSource={onRunSource}
            onSaveSourceSchedule={onSaveSourceSchedule}
            onToggleSource={onToggleSource}
            runningSourceId={runningSourceId}
            savingSourceId={savingSourceId}
            sourceDrafts={sourceDrafts}
            sourceName={sourceName}
            sources={sources}
            sourceUrl={sourceUrl}
            setSourceName={setSourceName}
            setSourceUrl={setSourceUrl}
            updateSourceDraft={updateSourceDraft}
          />
        ) : null}

        {activeSection === 'filters' ? (
          <section className="section-panel">
            <div className="panel-heading">
              <h3>Filtros</h3>
              <span>0</span>
            </div>
            <p className="empty-inline">Sin filtros configurados. Las reglas locales se implementaran en la siguiente vertical.</p>
          </section>
        ) : null}

        {activeSection === 'runs' ? <RunsView getSourceName={getSourceName} runs={runs} /> : null}

        {activeSection === 'settings' ? (
          <SettingsView onToggleScheduler={onToggleScheduler} savingScheduler={savingScheduler} scheduler={scheduler} />
        ) : null}
      </section>
    </main>
  );
}

function ResultsView({
  filters,
  itemPage,
  loading,
  pageSize,
  sources,
  onApply,
  onClear,
  onFilterChange,
  onPageChange,
  onPageSizeChange
}: {
  filters: ResultFilters;
  itemPage: Page<ItemResult>;
  loading: boolean;
  pageSize: number;
  sources: SearchSource[];
  onApply: () => void;
  onClear: () => void;
  onFilterChange: (field: keyof ResultFilters, value: string) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  const activeFilterCount = countActiveFilters(filters);
  const filterSummaries = summarizeFilters(filters, sources);

  function applyFilters() {
    onApply();
    setFiltersOpen(false);
  }

  function clearFilters() {
    onClear();
    setFiltersOpen(false);
  }

  return (
    <section className="results-view">
      <div className="results-controls">
        <button className="filter-toggle" type="button" onClick={() => setFiltersOpen(true)}>
          <SlidersHorizontal size={17} />
          Filtros
          {activeFilterCount > 0 ? <span>{activeFilterCount}</span> : null}
        </button>
        <div className="filter-summary" aria-live="polite">
          {filterSummaries.length > 0 ? filterSummaries.map((summary) => <span key={summary}>{summary}</span>) : <span>Sin filtros activos</span>}
        </div>
      </div>

      {filtersOpen ? <button className="filter-backdrop" type="button" aria-label="Cerrar filtros" onClick={() => setFiltersOpen(false)} /> : null}

      <section className={filtersOpen ? 'filter-panel open' : 'filter-panel'}>
        <div className="filter-panel-heading">
          <h3>Filtros de resultados</h3>
          <button type="button" title="Cerrar filtros" onClick={() => setFiltersOpen(false)}>
            <X size={17} />
          </button>
        </div>
        <form
          className="result-filters"
          onSubmit={(event) => {
            event.preventDefault();
            applyFilters();
          }}
        >
          <label>
            Fuente
            <select value={filters.sourceId} onChange={(event) => onFilterChange('sourceId', event.target.value)}>
              <option value="">Todas</option>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Desde
            <input
              type="datetime-local"
              value={filters.scrapedFrom}
              onChange={(event) => onFilterChange('scrapedFrom', event.target.value)}
            />
          </label>
          <label>
            Hasta
            <input type="datetime-local" value={filters.scrapedTo} onChange={(event) => onFilterChange('scrapedTo', event.target.value)} />
          </label>
          <label>
            Precio min
            <input min="0" type="number" value={filters.priceMin} onChange={(event) => onFilterChange('priceMin', event.target.value)} />
          </label>
          <label>
            Precio max
            <input min="0" type="number" value={filters.priceMax} onChange={(event) => onFilterChange('priceMax', event.target.value)} />
          </label>
          <button type="submit" disabled={loading}>
            <Search size={17} />
            Aplicar
          </button>
          <button type="button" disabled={loading} onClick={clearFilters}>
            <RotateCcw size={17} />
            Limpiar
          </button>
        </form>
      </section>

      <div className="table-wrap result-table">
        <table>
          <thead>
            <tr>
              <th>Articulo</th>
              <th>Fuente</th>
              <th>Scrape</th>
              <th>Marca</th>
              <th>Talla</th>
              <th>Estado</th>
              <th>Precio</th>
              <th>Favs</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {itemPage.items.length === 0 ? (
              <tr>
                <td colSpan={9} className="empty">
                  No hay resultados para los filtros actuales.
                </td>
              </tr>
            ) : (
              itemPage.items.map((item) => <ResultTableRow item={item} key={item.id} />)
            )}
          </tbody>
        </table>
      </div>

      <div className="result-cards">
        {itemPage.items.length === 0 ? (
          <p className="empty-inline">No hay resultados para los filtros actuales.</p>
        ) : (
          itemPage.items.map((item) => <ResultCard item={item} key={item.id} />)
        )}
      </div>

      <Pagination
        page={itemPage.page}
        pageSize={pageSize}
        total={itemPage.total}
        totalPages={itemPage.total_pages}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </section>
  );
}

function ResultTableRow({ item }: { item: ItemResult }) {
  return (
    <tr>
      <td>
        <ItemCell item={item} />
      </td>
      <td>{item.last_scraped_source_name}</td>
      <td>{formatDate(item.last_scraped_at)}</td>
      <td>{item.brand ?? '-'}</td>
      <td>{item.size ?? '-'}</td>
      <td>{item.status ?? '-'}</td>
      <td>{formatPrice(item)}</td>
      <td>{item.favorite_count ?? '-'}</td>
      <td>
        <RowActions item={item} />
      </td>
    </tr>
  );
}

function ResultCard({ item }: { item: ItemResult }) {
  return (
    <article className="result-card">
      <ItemCell item={item} />
      <dl>
        <div>
          <dt>Precio</dt>
          <dd>{formatPrice(item)}</dd>
        </div>
        <div>
          <dt>Fuente</dt>
          <dd>{item.last_scraped_source_name}</dd>
        </div>
        <div>
          <dt>Scrape</dt>
          <dd>{formatDate(item.last_scraped_at)}</dd>
        </div>
        <div>
          <dt>Detalle</dt>
          <dd>{[item.brand, item.size, item.status].filter(Boolean).join(' / ') || '-'}</dd>
        </div>
      </dl>
      <RowActions item={item} />
    </article>
  );
}

function OpportunitiesView({
  loading,
  opportunityPage,
  onPageChange
}: {
  loading: boolean;
  opportunityPage: Page<OpportunityResult>;
  onPageChange: (page: number) => void;
}) {
  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Oportunidades</h3>
        <span>{opportunityPage.total}</span>
      </div>
      {opportunityPage.items.length === 0 ? (
        <p className="empty-inline">Todavia no hay oportunidades. Se crearan cuando implementemos reglas locales.</p>
      ) : (
        <div className="opportunity-list">
          {opportunityPage.items.map((opportunity) => (
            <article className="opportunity-row" key={opportunity.id}>
              <ItemCell item={opportunity.item} />
              <span>{opportunity.source_name}</span>
              <span>{formatDate(opportunity.created_at)}</span>
              <RowActions item={opportunity.item} />
            </article>
          ))}
        </div>
      )}
      <Pagination
        page={opportunityPage.page}
        pageSize={opportunityPage.page_size}
        total={opportunityPage.total}
        totalPages={opportunityPage.total_pages}
        onPageChange={onPageChange}
        disabled={loading}
      />
    </section>
  );
}

function SourcesView({
  onCreateSource,
  onRunSource,
  onSaveSourceSchedule,
  onToggleSource,
  runningSourceId,
  savingSourceId,
  sourceDrafts,
  sourceName,
  sources,
  sourceUrl,
  setSourceName,
  setSourceUrl,
  updateSourceDraft
}: {
  onCreateSource: (event: FormEvent<HTMLFormElement>) => void;
  onRunSource: (sourceId: number) => void;
  onSaveSourceSchedule: (source: SearchSource) => void;
  onToggleSource: (source: SearchSource) => void;
  runningSourceId: number | null;
  savingSourceId: number | null;
  sourceDrafts: Record<number, SourceDraft>;
  sourceName: string;
  sources: SearchSource[];
  sourceUrl: string;
  setSourceName: (value: string) => void;
  setSourceUrl: (value: string) => void;
  updateSourceDraft: (sourceId: number, field: keyof SourceDraft, value: string) => void;
}) {
  return (
    <section className="sources-panel">
      <div className="panel-heading">
        <h3>Fuentes de busqueda</h3>
        <span>{sources.length}</span>
      </div>
      <form className="source-form" onSubmit={onCreateSource}>
        <input value={sourceName} onChange={(event) => setSourceName(event.target.value)} placeholder="Nombre de busqueda" required />
        <input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="URL de catalogo Vinted" required />
        <button type="submit">Guardar URL</button>
      </form>
      {sources.length === 0 ? (
        <p className="empty-inline">No hay fuentes configuradas.</p>
      ) : (
        <div className="sources-list">
          {sources.map((source) => (
            <article className="source-row" key={source.id}>
              <div className="source-main">
                <strong>{source.name}</strong>
                <a href={source.url} target="_blank" rel="noreferrer">
                  {source.url}
                </a>
              </div>
              <div className="source-schedule">
                <label>
                  Intervalo
                  <input
                    type="number"
                    min="60"
                    max="3600"
                    value={(sourceDrafts[source.id] ?? buildSourceDraft(source)).intervalSeconds}
                    onChange={(event) => updateSourceDraft(source.id, 'intervalSeconds', event.target.value)}
                  />
                </label>
                <label>
                  Jitter %
                  <input
                    type="number"
                    min="0"
                    max="50"
                    value={(sourceDrafts[source.id] ?? buildSourceDraft(source)).jitterPercent}
                    onChange={(event) => updateSourceDraft(source.id, 'jitterPercent', event.target.value)}
                  />
                </label>
                <label>
                  Ventanas
                  <input
                    value={(sourceDrafts[source.id] ?? buildSourceDraft(source)).allowedWindows}
                    placeholder="09:00-23:00"
                    onChange={(event) => updateSourceDraft(source.id, 'allowedWindows', event.target.value)}
                  />
                </label>
                <button type="button" disabled={savingSourceId === source.id} title="Guardar cadencia" onClick={() => onSaveSourceSchedule(source)}>
                  <Save size={16} />
                  Guardar
                </button>
              </div>
              <button
                type="button"
                disabled={!source.is_active || runningSourceId !== null}
                title={source.is_active ? 'Ejecutar esta fuente' : 'La fuente esta pausada'}
                onClick={() => onRunSource(source.id)}
              >
                <Play size={17} />
                {runningSourceId === source.id ? 'Ejecutando' : 'Ejecutar'}
              </button>
              <button
                type="button"
                disabled={savingSourceId === source.id}
                title={source.is_active ? 'Pausar fuente' : 'Activar fuente'}
                onClick={() => onToggleSource(source)}
              >
                <Power size={16} />
                {source.is_active ? 'Pausar' : 'Activar'}
              </button>
              <span className={source.is_active ? 'status active' : 'status'}>{source.is_active ? 'Activa' : 'Pausada'}</span>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function RunsView({ getSourceName, runs }: { getSourceName: (sourceId: number) => string; runs: Run[] }) {
  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Runs</h3>
        <span>{runs.length}</span>
      </div>
      {runs.length === 0 ? (
        <p className="empty-inline">Sin ejecuciones registradas.</p>
      ) : (
        <div className="runs-list">
          {runs.map((run) => (
            <article className="run-row" key={run.id}>
              <div>
                <strong>{getSourceName(run.source_id)}</strong>
                <span>{formatDate(run.started_at)}</span>
                {run.error_message ? <p>{run.error_message}</p> : null}
              </div>
              <dl>
                <div>
                  <dt>Estado</dt>
                  <dd className={`run-status ${run.status}`}>{run.status}</dd>
                </div>
                <div>
                  <dt>Trigger</dt>
                  <dd>{run.trigger}</dd>
                </div>
                <div>
                  <dt>Encontrados</dt>
                  <dd>{run.items_found}</dd>
                </div>
                <div>
                  <dt>Nuevos</dt>
                  <dd>{run.items_new}</dd>
                </div>
                <div>
                  <dt>Oportunidades</dt>
                  <dd>{run.opportunities_created}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function SettingsView({
  onToggleScheduler,
  savingScheduler,
  scheduler
}: {
  onToggleScheduler: () => void;
  savingScheduler: boolean;
  scheduler: SchedulerState | null;
}) {
  return (
    <section className="section-panel">
      <div className="panel-heading">
        <h3>Settings</h3>
        <span>{scheduler?.effective_enabled ? 'Scheduler activo' : 'Scheduler parado'}</span>
      </div>
      {scheduler ? (
        <div className="settings-grid">
          <div>
            <strong>Scheduler</strong>
            <p>{scheduler.enabled ? 'Habilitado en la UI' : 'Deshabilitado en la UI'}</p>
          </div>
          <div>
            <strong>Runtime</strong>
            <p>{scheduler.runtime_enabled ? 'Permitido por .env' : 'Bloqueado por .env'}</p>
          </div>
          <div>
            <strong>Concurrencia</strong>
            <p>{scheduler.max_concurrent_runs} global / {scheduler.per_source_concurrency} por fuente</p>
          </div>
          <div>
            <strong>Zona horaria</strong>
            <p>{scheduler.timezone}</p>
          </div>
          <div>
            <strong>Proxy Vinted</strong>
            <p>{scheduler.proxy_enabled ? (scheduler.proxy_configured ? 'Activo y configurado' : 'Activo sin URL') : 'Desactivado'}</p>
          </div>
          <button type="button" disabled={savingScheduler} onClick={onToggleScheduler}>
            <Power size={17} />
            {scheduler.enabled ? 'Deshabilitar scheduler' : 'Habilitar scheduler'}
          </button>
        </div>
      ) : (
        <p className="empty-inline">No se pudo cargar la configuracion del scheduler.</p>
      )}
    </section>
  );
}

function ItemCell({ item }: { item: Item }) {
  return (
    <div className="item-cell">
      {item.image_url ? <img src={item.image_url} alt="" /> : <div className="thumb" />}
      <span>{item.title}</span>
    </div>
  );
}

function RowActions({ item }: { item: Item }) {
  return (
    <div className="row-actions">
      <a href={item.url} target="_blank" rel="noreferrer" title="Ver en Vinted">
        <ExternalLink size={17} />
      </a>
      <button type="button" title="Marcar favorito" disabled>
        <Heart size={17} />
      </button>
      <button type="button" title="Comprar" disabled>
        <ShoppingCart size={17} />
      </button>
    </div>
  );
}

function Pagination({
  disabled = false,
  onPageChange,
  page,
  pageSize,
  total,
  totalPages,
  onPageSizeChange
}: {
  disabled?: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}) {
  const firstItem = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastItem = Math.min(page * pageSize, total);
  return (
    <div className="pagination">
      <span>
        Mostrando {firstItem}-{lastItem} de {total}
      </span>
      <div>
        {onPageSizeChange ? (
          <label>
            Resultados por pagina
            <select value={pageSize} disabled={disabled} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </label>
        ) : null}
        <button type="button" disabled={disabled || page <= 1} onClick={() => onPageChange(page - 1)}>
          <ChevronLeft size={17} />
          Anterior
        </button>
        <span>Pagina {page} de {totalPages || 1}</span>
        <button type="button" disabled={disabled || totalPages === 0 || page >= totalPages} onClick={() => onPageChange(page + 1)}>
          Siguiente
          <ChevronRight size={17} />
        </button>
      </div>
    </div>
  );
}

function buildSourceDrafts(sources: SearchSource[]): Record<number, SourceDraft> {
  return Object.fromEntries(sources.map((source) => [source.id, buildSourceDraft(source)]));
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

function buildSourceDraft(source: SearchSource): SourceDraft {
  const config = source.scheduler_config ?? {};
  return {
    intervalSeconds: String(config.interval_seconds ?? 300),
    jitterPercent: String(config.jitter_percent ?? 20),
    allowedWindows: (config.allowed_windows ?? []).join(', ')
  };
}

function buildItemQuery(filters: ResultFilters, page: number, pageSize: number): ItemQuery {
  return {
    page,
    page_size: pageSize,
    source_id: filters.sourceId ? Number(filters.sourceId) : null,
    scraped_from: toApiDateTime(filters.scrapedFrom),
    scraped_to: toApiDateTime(filters.scrapedTo),
    price_min: filters.priceMin,
    price_max: filters.priceMax
  };
}

function countActiveFilters(filters: ResultFilters): number {
  return [filters.sourceId, filters.scrapedFrom, filters.scrapedTo, filters.priceMin, filters.priceMax].filter(Boolean).length;
}

function summarizeFilters(filters: ResultFilters, sources: SearchSource[]): string[] {
  const summaries: string[] = [];
  if (filters.sourceId) {
    summaries.push(sources.find((source) => source.id === Number(filters.sourceId))?.name ?? `Fuente ${filters.sourceId}`);
  }
  if (filters.scrapedFrom) {
    summaries.push(`Desde ${formatDate(new Date(filters.scrapedFrom).toISOString())}`);
  }
  if (filters.scrapedTo) {
    summaries.push(`Hasta ${formatDate(new Date(filters.scrapedTo).toISOString())}`);
  }
  if (filters.priceMin) {
    summaries.push(`Min ${filters.priceMin}`);
  }
  if (filters.priceMax) {
    summaries.push(`Max ${filters.priceMax}`);
  }
  return summaries;
}

function toApiDateTime(value: string): string | undefined {
  if (!value) {
    return undefined;
  }
  return new Date(value).toISOString();
}

function formatPrice(item: Item): string {
  return item.price_amount ? `${item.price_amount} ${item.currency ?? ''}` : '-';
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('es-ES', {
    dateStyle: 'short',
    timeStyle: 'medium'
  }).format(new Date(value));
}
