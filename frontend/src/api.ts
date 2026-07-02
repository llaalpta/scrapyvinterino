const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '';

export type SearchSource = {
  id: number;
  name: string;
  url: string;
  normalized_query: Record<string, string[]>;
  is_active: boolean;
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
  first_seen_at: string;
  last_seen_at: string;
};

export type Run = {
  id: number;
  source_id: number;
  status: string;
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

export function fetchItems(): Promise<Item[]> {
  return getJson<Item[]>('/api/items');
}

export function fetchRuns(): Promise<Run[]> {
  return getJson<Run[]>('/api/runs');
}

export function runSource(sourceId: number): Promise<Run> {
  return postJson<Run>(`/api/sources/${sourceId}/runs`);
}
