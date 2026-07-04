import { RotateCcw, Search, SlidersHorizontal, X } from 'lucide-react';
import { useState } from 'react';
import type { OpportunityResult, Page, SearchSource } from '../../api';
import { ItemCell } from '../../components/ItemCell';
import { Pagination } from '../../components/Pagination';
import { RowActions } from '../../components/RowActions';
import { formatDate, formatPrice } from '../../utils/format';
import { countActiveFilters, evaluationLabel, summarizeFilters, type ResultFilters } from '../results/resultFilters';

export function OpportunitiesView({
  filters,
  loading,
  opportunityPage,
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
  loading: boolean;
  opportunityPage: Page<OpportunityResult>;
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
          <h3>Filtros de oportunidades</h3>
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
            Monitor
            <select value={filters.sourceId} onChange={(event) => onFilterChange('sourceId', event.target.value)}>
              <option value="">Todos</option>
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
          <label>
            Estado
            <select value={filters.evaluationStatus} onChange={(event) => onFilterChange('evaluationStatus', event.target.value)}>
              <option value="">Todos</option>
              <option value="passed">Filtrada OK</option>
              <option value="passed_without_filters">Sin filtros</option>
              <option value="passed_without_detail">Sin detalle</option>
              <option value="detail_error">Error detalle</option>
            </select>
          </label>
        </div>
      </form>

      <div className="table-wrap result-table">
        <table>
          <thead>
            <tr>
              <th>Articulo</th>
              <th>Monitor</th>
              <th>Estado</th>
              <th>Scrape</th>
              <th>Marca</th>
              <th>Talla</th>
              <th>Precio</th>
              <th>Favs</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {opportunityPage.items.length === 0 ? (
              <tr>
                <td colSpan={9} className="empty">
                  No hay oportunidades para los filtros actuales.
                </td>
              </tr>
            ) : (
              opportunityPage.items.map((opportunity) => <OpportunityTableRow key={opportunity.id} opportunity={opportunity} />)
            )}
          </tbody>
        </table>
      </div>

      <div className="result-cards">
        {opportunityPage.items.length === 0 ? (
          <p className="empty-inline">No hay oportunidades para los filtros actuales.</p>
        ) : (
          opportunityPage.items.map((opportunity) => <OpportunityCard key={opportunity.id} opportunity={opportunity} />)
        )}
      </div>

      <Pagination
        page={opportunityPage.page}
        pageSize={pageSize}
        total={opportunityPage.total}
        totalPages={opportunityPage.total_pages}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        disabled={loading}
      />
    </section>
  );
}

function OpportunityTableRow({ opportunity }: { opportunity: OpportunityResult }) {
  return (
    <tr>
      <td>
        <ItemCell item={opportunity.item} />
      </td>
      <td>{opportunity.source_name}</td>
      <td>
        <span className={`status evaluation ${opportunity.evaluation_status}`}>{evaluationLabel(opportunity.evaluation_status)}</span>
      </td>
      <td>{formatDate(opportunity.last_scraped_at)}</td>
      <td>{opportunity.item.brand ?? '-'}</td>
      <td>{opportunity.item.size ?? '-'}</td>
      <td>{formatPrice(opportunity.item)}</td>
      <td>{opportunity.item.favorite_count ?? '-'}</td>
      <td>
        <RowActions item={opportunity.item} />
      </td>
    </tr>
  );
}

function OpportunityCard({ opportunity }: { opportunity: OpportunityResult }) {
  return (
    <article className="result-card">
      <ItemCell item={opportunity.item} />
      <dl>
        <div>
          <dt>Precio</dt>
          <dd>{formatPrice(opportunity.item)}</dd>
        </div>
        <div>
          <dt>Monitor</dt>
          <dd>{opportunity.source_name}</dd>
        </div>
        <div>
          <dt>Estado</dt>
          <dd>{evaluationLabel(opportunity.evaluation_status)}</dd>
        </div>
        <div>
          <dt>Scrape</dt>
          <dd>{formatDate(opportunity.last_scraped_at)}</dd>
        </div>
      </dl>
      <RowActions item={opportunity.item} />
    </article>
  );
}
