import { RotateCcw, Search, SlidersHorizontal, X } from 'lucide-react';
import { useState } from 'react';
import type { ItemResult, Page, SearchSource } from '../../api';
import { ItemCell } from '../../components/ItemCell';
import { Pagination } from '../../components/Pagination';
import { RowActions } from '../../components/RowActions';
import { formatDate, formatPrice } from '../../utils/format';
import { countActiveFilters, summarizeFilters, type ResultFilters } from './resultFilters';

export function ResultsView({
  filters,
  itemPage,
  loading,
  pageSize,
  sources,
  onApply,
  onApplyFilters,
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
  onApplyFilters: (filters: ResultFilters) => void;
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

  function removeFilter(field: keyof ResultFilters) {
    const nextFilters = { ...filters, [field]: '' };
    onFilterChange(field, '');
    setFiltersOpen(false);
    onApplyFilters(nextFilters);
  }

  return (
    <section className="results-view">
      <div className="results-controls">
        <button className="filter-toggle" type="button" aria-expanded={filtersOpen} onClick={() => setFiltersOpen((current) => !current)}>
          <SlidersHorizontal size={17} />
          Filtros
          {activeFilterCount > 0 ? <span>{activeFilterCount}</span> : null}
        </button>
        <div className="filter-summary" aria-live="polite">
          {filterSummaries.length > 0 ? (
            filterSummaries.map((summary) => (
              <button key={summary.field} type="button" title={`Quitar ${summary.label}`} onClick={() => removeFilter(summary.field)}>
                {summary.label}
                <X size={14} />
              </button>
            ))
          ) : (
            <span>Sin filtros activos</span>
          )}
        </div>
      </div>

      {filtersOpen ? <button className="filter-backdrop" type="button" aria-label="Cerrar filtros" onClick={() => setFiltersOpen(false)} /> : null}

      <form
        className={filtersOpen ? 'filter-panel open' : 'filter-panel'}
        onSubmit={(event) => {
          event.preventDefault();
          applyFilters();
        }}
      >
        <div className="filter-panel-heading">
          <h3>Filtros de resultados</h3>
          <div className="filter-panel-actions">
            <button type="button" disabled={loading || activeFilterCount === 0} onClick={clearFilters}>
              <RotateCcw size={17} />
              Limpiar
            </button>
            <button type="submit" disabled={loading}>
              <Search size={17} />
              Aplicar
            </button>
            <button className="icon-button" type="button" title="Cerrar filtros" onClick={() => setFiltersOpen(false)}>
              <X size={17} />
            </button>
          </div>
        </div>
        <div className="result-filters">
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
        </div>
      </form>

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
