import type { Item } from '../../api';
import { availabilityLabel, availabilityState } from './opportunityAvailability';

export function AvailabilityBadge({ item }: { item: Item }) {
  const state = availabilityState(item);
  const label = availabilityLabel(item);
  return (
    <span aria-label={`Disponibilidad publica: ${label}`} className={`availability-badge ${state}`}>
      Publica: {label}
    </span>
  );
}
