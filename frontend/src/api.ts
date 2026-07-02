const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '';

export type SearchSource = {
  id: number;
  name: string;
  url: string;
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
};

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`);
  if (!response.ok) {
    throw new Error(`API error ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchSources(): Promise<SearchSource[]> {
  return getJson<SearchSource[]>('/api/sources');
}

export async function createSource(payload: { name: string; url: string }): Promise<SearchSource> {
  const response = await fetch(`${apiBaseUrl}/api/sources`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new Error(`API error ${response.status}`);
  }
  return response.json() as Promise<SearchSource>;
}

export function fetchItems(): Promise<Item[]> {
  return getJson<Item[]>('/api/items');
}
