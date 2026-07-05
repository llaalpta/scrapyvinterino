const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '';

export type SearchSource = {
  id: number;
  name: string;
  url: string;
  normalized_query: Record<string, string[]>;
  is_active: boolean;
  scheduler_config: SourceSchedulerConfig;
  monitor_mode: 'manual' | 'continuous' | 'duration' | 'window';
  duration_minutes: number | null;
  filter_rule_ids: number[];
  monitor_started_at: string | null;
  monitor_until: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  archived_at: string | null;
};

export type FilterRule = {
  id: number;
  source_id: number | null;
  name: string;
  definition: {
    blacklist_terms?: string[];
  };
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type ProxyProfile = {
  id: number;
  name: string;
  scheme: string;
  kind: 'own' | 'datacenter' | 'residential';
  host: string;
  port: number;
  username: string | null;
  username_masked: string | null;
  has_password: boolean;
  password_fingerprint: string | null;
  is_active: boolean;
  max_concurrent_runs: number;
  cooldown_until: string | null;
  failure_count: number;
  last_used_at: string | null;
  last_test_status: string | null;
  last_test_ip: string | null;
  last_test_error: string | null;
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
  allow_direct_without_proxy: boolean;
  direct_max_concurrent_runs: number;
  active_proxy_count: number;
  proxy_capacity: number;
  direct_capacity: number;
  effective_capacity: number;
  active_periodic_monitors: number;
  catalog_per_page: number;
  detail_max_candidates_per_run: number;
  request_timeout_ms: number;
  stop_monitor_after_consecutive_failures: number;
  proxy_cooldown_minutes: number;
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

export type OpportunityResult = {
  id: number;
  item: Item;
  source_id: number;
  source_name: string;
  rule_id: number | null;
  status: string;
  evaluation_status: string;
  filter_snapshot: Array<{ id: number; name: string; definition: Record<string, unknown> }>;
  score: string | null;
  created_at: string;
  last_scraped_at: string;
  last_run_id: number | null;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
};

export type OpportunityQuery = {
  page?: number;
  page_size?: number;
  source_id?: number | null;
  scraped_from?: string;
  scraped_to?: string;
  price_min?: string;
  price_max?: string;
  evaluation_status?: string;
};

export type Run = {
  id: number;
  source_id: number;
  monitor_session_id: number | null;
  status: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  items_found: number;
  items_new: number;
  items_filter_passed: number;
  items_discarded_by_filters: number;
  items_filter_pending: number;
  opportunities_created: number;
  error_message: string | null;
  runtime_metadata: Record<string, unknown>;
};

export type MonitorStatsRange = 'minutes' | 'hours' | 'days' | 'month' | 'all';

export type MonitorStatsSummary = {
  sessions_count: number;
  active_seconds: number;
  runs_count: number;
  failed_runs: number;
  items_found: number;
  items_new: number;
  items_discarded_by_filters: number;
  opportunities_created: number;
};

export type MonitorSession = {
  id: number;
  started_at: string;
  stopped_at: string | null;
  stop_reason: string | null;
  duration_seconds: number;
};

export type MonitorChartPoint = {
  bucket_start: string;
  bucket_end: string;
  items_found: number;
  runs_count: number;
};

export type MonitorStats = {
  range: MonitorStatsRange;
  range_start: string;
  range_end: string;
  bucket_label: string;
  bucket_seconds: number | null;
  active_session: MonitorSession | null;
  latest_session: MonitorSession | null;
  session_summary: MonitorStatsSummary;
  historical_summary: MonitorStatsSummary;
  chart_points: MonitorChartPoint[];
};

export type RunEvent = {
  id: number;
  run_id: number | null;
  source_id: number | null;
  phase: string;
  level: 'debug' | 'info' | 'warning' | 'error';
  method: string | null;
  url: string | null;
  status_code: number | null;
  duration_ms: number | null;
  proxy_profile_id: number | null;
  egress_ip: string | null;
  user_agent: string | null;
  auth_mode: string | null;
  message: string | null;
  details: Record<string, unknown>;
  created_at: string;
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
  return getJson<SearchSource[]>('/api/monitors');
}

export async function createSource(payload: { name: string; url: string }): Promise<SearchSource> {
  return postJson<SearchSource>('/api/monitors', payload);
}

export function updateSource(
  sourceId: number,
  payload: {
    name?: string;
    url?: string;
    is_active?: boolean;
    scheduler_config?: SourceSchedulerConfig;
    monitor_mode?: SearchSource['monitor_mode'];
    duration_minutes?: number | null;
    filter_rule_ids?: number[];
  }
): Promise<SearchSource> {
  return patchJson<SearchSource>(`/api/monitors/${sourceId}`, payload);
}

export async function deleteSource(sourceId: number): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/monitors/${sourceId}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
}

export function fetchScheduler(): Promise<SchedulerState> {
  return getJson<SchedulerState>('/api/scheduler');
}

export type SchedulerUpdate = Partial<{
  enabled: boolean;
  max_concurrent_runs: number;
  allow_direct_without_proxy: boolean;
  direct_max_concurrent_runs: number;
  catalog_per_page: number;
  detail_max_candidates_per_run: number;
  request_timeout_ms: number;
  stop_monitor_after_consecutive_failures: number;
  proxy_cooldown_minutes: number;
}>;

export function updateScheduler(payload: SchedulerUpdate): Promise<SchedulerState> {
  return patchJson<SchedulerState>('/api/scheduler', payload);
}

export function fetchFilterRules(): Promise<FilterRule[]> {
  return getJson<FilterRule[]>('/api/filter-rules');
}

export function createFilterRule(payload: { name: string; definition: { blacklist_terms: string[] }; is_active?: boolean }): Promise<FilterRule> {
  return postJson<FilterRule>('/api/filter-rules', payload);
}

export function updateFilterRule(
  ruleId: number,
  payload: { name?: string; definition?: { blacklist_terms: string[] }; is_active?: boolean }
): Promise<FilterRule> {
  return patchJson<FilterRule>(`/api/filter-rules/${ruleId}`, payload);
}

export function fetchProxyProfiles(): Promise<ProxyProfile[]> {
  return getJson<ProxyProfile[]>('/api/proxy-profiles');
}

export function createProxyProfile(payload: {
  name: string;
  scheme: string;
  kind: ProxyProfile['kind'];
  host: string;
  port: number;
  username?: string;
  password?: string;
  max_concurrent_runs?: number;
  is_active?: boolean;
}): Promise<ProxyProfile> {
  return postJson<ProxyProfile>('/api/proxy-profiles', payload);
}

export function updateProxyProfile(profileId: number, payload: Partial<Pick<ProxyProfile, 'is_active' | 'max_concurrent_runs' | 'kind'>>): Promise<ProxyProfile> {
  return patchJson<ProxyProfile>(`/api/proxy-profiles/${profileId}`, payload);
}

export function testProxyProfile(profileId: number): Promise<ProxyProfile> {
  return postJson<ProxyProfile>(`/api/proxy-profiles/${profileId}/test`);
}

export function startMonitor(sourceId: number): Promise<Run> {
  return postJson<Run>(`/api/monitors/${sourceId}/start`);
}

export function stopMonitor(sourceId: number): Promise<SearchSource> {
  return postJson<SearchSource>(`/api/monitors/${sourceId}/stop`);
}

export function runMonitor(sourceId: number): Promise<Run> {
  return postJson<Run>(`/api/monitors/${sourceId}/runs`);
}

export function fetchOpportunities(query: OpportunityQuery = {}): Promise<Page<OpportunityResult>> {
  return getJson<Page<OpportunityResult>>(`/api/opportunities${toQueryString(query)}`);
}

export function fetchRuns(query: { limit?: number; source_id?: number } = {}): Promise<Run[]> {
  return getJson<Run[]>(`/api/runs${toQueryString(query)}`);
}

export function fetchMonitorStats(sourceId: number, range: MonitorStatsRange = 'hours'): Promise<MonitorStats> {
  return getJson<MonitorStats>(`/api/monitors/${sourceId}/stats?range=${range}`);
}

export function fetchRunEvents(runId: number): Promise<RunEvent[]> {
  return getJson<RunEvent[]>(`/api/runs/${runId}/events`);
}

export function monitorEventsStreamUrl(lastEventId = 0): string {
  return `${apiBaseUrl}/api/monitors/events/stream?last_event_id=${lastEventId}`;
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
