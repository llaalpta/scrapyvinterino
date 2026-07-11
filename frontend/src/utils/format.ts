import type { Item } from '../api';

export function formatPrice(item: Item): string {
  return formatMoney(item.price_amount, item.currency);
}

export function formatMoney(amount: string | null, currency: string | null): string {
  if (amount === null) {
    return '-';
  }
  const parsedAmount = Number(amount);
  if (!Number.isFinite(parsedAmount)) {
    return currency ? `${amount} ${currency}` : amount;
  }
  if (!currency || !/^[A-Z]{3}$/.test(currency)) {
    return new Intl.NumberFormat('es-ES', { maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(parsedAmount);
  }
  try {
    return new Intl.NumberFormat('es-ES', { currency, style: 'currency' }).format(parsedAmount);
  } catch {
    return `${amount} ${currency}`;
  }
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat('es-ES', {
    dateStyle: 'short',
    timeStyle: 'medium'
  }).format(new Date(value));
}
