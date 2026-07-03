import type { ItemQuery, SearchSource } from '../../api';
import { formatDate } from '../../utils/format';

export type ResultFilters = {
  sourceId: string;
  scrapedFrom: string;
  scrapedTo: string;
  priceMin: string;
  priceMax: string;
};

export const defaultFilters: ResultFilters = {
  sourceId: '',
  scrapedFrom: '',
  scrapedTo: '',
  priceMin: '',
  priceMax: ''
};

export type FilterSummary = {
  field: keyof ResultFilters;
  label: string;
};

export function buildItemQuery(filters: ResultFilters, page: number, pageSize: number): ItemQuery {
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

export function countActiveFilters(filters: ResultFilters): number {
  return [filters.sourceId, filters.scrapedFrom, filters.scrapedTo, filters.priceMin, filters.priceMax].filter(Boolean).length;
}

export function summarizeFilters(filters: ResultFilters, sources: SearchSource[]): FilterSummary[] {
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
  return summaries;
}

function toApiDateTime(value: string): string | undefined {
  if (!value) {
    return undefined;
  }
  return new Date(value).toISOString();
}
