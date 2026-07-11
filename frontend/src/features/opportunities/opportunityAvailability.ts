import type { Item } from '../../api';

const availabilityLabels: Record<string, string> = {
  buyable: 'Comprable',
  closed: 'Cerrado',
  draft: 'Borrador',
  hidden: 'Oculto',
  not_buyable: 'No comprable',
  not_permitted: 'Compra no permitida',
  processing: 'En revision',
  reserved: 'Reservado',
  shipping_unavailable: 'Sin envio',
  unknown: 'Sin confirmar'
};

const availabilityReasonLabels: Record<string, string> = {
  closed: 'Articulo cerrado',
  draft: 'Borrador',
  hidden: 'Articulo oculto',
  not_buyable: 'Compra publica no disponible',
  not_permitted: 'Transaccion no permitida',
  out_of_stock: 'Sin stock',
  processing: 'En revision',
  reserved: 'Reservado',
  shipping_unavailable: 'Envio no disponible',
  unknown: 'Sin confirmacion publica'
};

export function availabilityState(item: Item): string {
  const state = item.availability_flags.state;
  return typeof state === 'string' && state in availabilityLabels ? state : 'unknown';
}

export function availabilityLabel(item: Item): string {
  return availabilityLabels[availabilityState(item)];
}

export function availabilityReasons(item: Item): string[] {
  const reasons = item.availability_flags.reason_codes;
  if (!Array.isArray(reasons)) {
    return availabilityState(item) === 'buyable' ? [] : [availabilityReasonLabels.unknown];
  }
  return reasons
    .filter((reason): reason is string => typeof reason === 'string' && reason.length > 0)
    .map((reason) => availabilityReasonLabels[reason] ?? reason.replaceAll('_', ' '));
}
