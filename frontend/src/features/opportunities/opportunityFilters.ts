import type { OpportunityQuery, SearchSource } from '../../api';
import { formatDate } from '../../utils/format';

export type OpportunityFilters = {
  sourceId: string;
  scrapedFrom: string;
  scrapedTo: string;
  priceMin: string;
  priceMax: string;
  evaluationStatus: string;
};

export const defaultOpportunityFilters: OpportunityFilters = {
  sourceId: '',
  scrapedFrom: '',
  scrapedTo: '',
  priceMin: '',
  priceMax: '',
  evaluationStatus: ''
};

export type FilterSummary = {
  field: keyof OpportunityFilters;
  label: string;
};

export function buildOpportunityQuery(filters: OpportunityFilters, page: number, pageSize: number): OpportunityQuery {
  return {
    page,
    page_size: pageSize,
    source_id: filters.sourceId ? Number(filters.sourceId) : null,
    scraped_from: toApiDateTime(filters.scrapedFrom),
    scraped_to: toApiDateTime(filters.scrapedTo),
    price_min: filters.priceMin,
    price_max: filters.priceMax,
    evaluation_status: filters.evaluationStatus
  };
}

export function countActiveFilters(filters: OpportunityFilters): number {
  return [filters.sourceId, filters.scrapedFrom, filters.scrapedTo, filters.priceMin, filters.priceMax, filters.evaluationStatus].filter(Boolean)
    .length;
}

export function summarizeFilters(filters: OpportunityFilters, sources: SearchSource[]): FilterSummary[] {
  const summaries: FilterSummary[] = [];
  if (filters.sourceId) {
    summaries.push({
      field: 'sourceId',
      label: sources.find((source) => source.id === Number(filters.sourceId))?.name ?? `Monitor ${filters.sourceId}`
    });
  }
  if (filters.scrapedFrom) {
    summaries.push({ field: 'scrapedFrom', label: `Desde ${formatDate(new Date(filters.scrapedFrom).toISOString())}` });
  }
  if (filters.scrapedTo) {
    summaries.push({ field: 'scrapedTo', label: `Hasta ${formatDate(new Date(filters.scrapedTo).toISOString())}` });
  }
  if (filters.priceMin) {
    summaries.push({ field: 'priceMin', label: `Min ${filters.priceMin}` });
  }
  if (filters.priceMax) {
    summaries.push({ field: 'priceMax', label: `Max ${filters.priceMax}` });
  }
  if (filters.evaluationStatus) {
    summaries.push({ field: 'evaluationStatus', label: evaluationLabel(filters.evaluationStatus) });
  }
  return summaries;
}

export function evaluationLabel(status: string): string {
  if (status === 'passed_without_filters') {
    return 'Sin filtros';
  }
  if (status === 'passed_without_detail') {
    return 'Sin detalle';
  }
  if (status === 'detail_error') {
    return 'Error detalle';
  }
  if (status === 'passed') {
    return 'Filtrada OK';
  }
  return status;
}

function toApiDateTime(value: string): string | undefined {
  if (!value) {
    return undefined;
  }
  return new Date(value).toISOString();
}
