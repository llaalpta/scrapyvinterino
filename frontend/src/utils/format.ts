import type { Item } from '../api';

export function formatPrice(item: Item): string {
  return item.price_amount ? `${item.price_amount} ${item.currency ?? ''}` : '-';
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat('es-ES', {
    dateStyle: 'short',
    timeStyle: 'medium'
  }).format(new Date(value));
}
