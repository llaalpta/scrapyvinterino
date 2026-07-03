const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '';

export type SearchSource = {
  id: number;
  name: string;
  url: string;
  normalized_query: Record<string, string[]>;
  is_active: boolean;
  scheduler_config: SourceSchedulerConfig;
};

export type SourceSchedulerConfig = {
  interval_seconds?: number;
  jitter_percent?: number;
  allowed_windows?: string[];
};

export type SchedulerState = {
  enabled: boolean;
  runtime_enabled: boolean;
  effective_enabled: boolean;
  max_concurrent_runs: number;
  per_source_concurrency: number;
  poll_interval_seconds: number;
  timezone: string;
  proxy_enabled: boolean;
  proxy_configured: boolean;
};

export type Item = {
  id: number;
  vinted_item_id: string;
  title: string;
  brand: string | null;
  price_amount: string | null;
  currency: string | null;
  size: string | null;
  status: string | null;
  seller_login: string | null;
  seller_country: string | null;
  favorite_count: number | null;
  url: string;
  image_url: string | null;
  description: string | null;
  color: string | null;
  category: string | null;
  shipping_price_amount: string | null;
  buyer_protection_fee_amount: string | null;
  total_price_amount: string | null;
  photos: string[];
  seller_rating: string | null;
  seller_badges: string[];
  availability_flags: Record<string, unknown>;
  detail_last_fetched_at: string | null;
  detail_error: string | null;
  first_seen_at: string;
  last_seen_at: string;
};

export type ItemResult = Item & {
  last_scraped_at: string;
  last_scraped_source_id: number;
  last_scraped_source_name: string;
  last_run_id: number;
};

export type OpportunityResult = {
  id: number;
  item: Item;
  source_id: number;
  source_name: string;
  rule_id: number;
  status: string;
  score: string | null;
  created_at: string;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

export type ItemQuery = {
  page?: number;
  page_size?: number;
  source_id?: number | null;
  scraped_from?: string;
  scraped_to?: string;
  price_min?: string;
  price_max?: string;
};

export type Run = {
  id: number;
  source_id: number;
  status: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  items_found: number;
  items_new: number;
  opportunities_created: number;
  error_message: string | null;
};

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`);
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, payload?: unknown): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: payload === undefined ? undefined : JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

async function patchJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

async function getErrorMessage(response: Response): Promise<string> {
  const fallback = `API error ${response.status}`;
  const body = await response.json().catch(() => null) as { detail?: unknown } | null;

  if (!body?.detail) {
    return fallback;
  }

  if (typeof body.detail === 'string') {
    return body.detail;
  }

  if (Array.isArray(body.detail)) {
    const messages = body.detail
      .map((entry) => {
        if (entry && typeof entry === 'object' && 'msg' in entry) {
          return String(entry.msg);
        }
        return null;
      })
      .filter(Boolean);

    if (messages.length > 0) {
      return messages.join(', ');
    }
  }

  return fallback;
}

export function fetchSources(): Promise<SearchSource[]> {
  return getJson<SearchSource[]>('/api/sources');
}

export async function createSource(payload: { name: string; url: string }): Promise<SearchSource> {
  return postJson<SearchSource>('/api/sources', payload);
}

export function updateSource(
  sourceId: number,
  payload: { is_active?: boolean; scheduler_config?: SourceSchedulerConfig }
): Promise<SearchSource> {
  return patchJson<SearchSource>(`/api/sources/${sourceId}`, payload);
}

export function fetchScheduler(): Promise<SchedulerState> {
  return getJson<SchedulerState>('/api/scheduler');
}

export function updateScheduler(payload: { enabled: boolean }): Promise<SchedulerState> {
  return patchJson<SchedulerState>('/api/scheduler', payload);
}

export function fetchItems(query: ItemQuery = {}): Promise<Page<ItemResult>> {
  return getJson<Page<ItemResult>>(`/api/items${toQueryString(query)}`);
}

export function fetchOpportunities(query: { page?: number; page_size?: number } = {}): Promise<Page<OpportunityResult>> {
  return getJson<Page<OpportunityResult>>(`/api/opportunities${toQueryString(query)}`);
}

export function fetchRuns(): Promise<Run[]> {
  return getJson<Run[]>('/api/runs');
}

export function runSource(sourceId: number): Promise<Run> {
  return postJson<Run>(`/api/sources/${sourceId}/runs`);
}

function toQueryString(query: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value));
    }
  });
  const serialized = params.toString();
  return serialized ? `?${serialized}` : '';
}
