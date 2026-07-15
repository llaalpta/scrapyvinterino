export type CollectionLoadState = 'loading' | 'ready' | 'unavailable';

export function markCollectionUnavailable(current: CollectionLoadState): CollectionLoadState {
  return current === 'ready' ? current : 'unavailable';
}
