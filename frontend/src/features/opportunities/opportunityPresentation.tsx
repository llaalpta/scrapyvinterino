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

export function AvailabilityBadge({ item }: { item: Item }) {
  const state = availabilityState(item);
  return <span className={`availability-badge ${state}`}>{availabilityLabels[state]}</span>;
}

function availabilityState(item: Item): string {
  const state = item.availability_flags.state;
  return typeof state === 'string' && state in availabilityLabels ? state : 'unknown';
}
