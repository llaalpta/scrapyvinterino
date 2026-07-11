import { ExternalLink, Heart, ShoppingCart } from 'lucide-react';
import { type ReactNode, useId } from 'react';
import type { Item } from '../api';

export function RowActions({ item }: { item: Item }) {
  return (
    <div className="row-actions">
      <a aria-label={`Ver ${item.title} en Vinted`} href={item.url} target="_blank" rel="noreferrer" title="Ver en Vinted">
        <ExternalLink size={17} />
      </a>
      <UnavailableAction label="Favorito no disponible">
        <Heart size={17} />
      </UnavailableAction>
      <UnavailableAction label="Compra autenticada no disponible">
        <ShoppingCart size={17} />
      </UnavailableAction>
    </div>
  );
}

function UnavailableAction({ children, label }: { children: ReactNode; label: string }) {
  const reasonId = useId();
  return (
    <span
      aria-describedby={reasonId}
      aria-disabled="true"
      aria-label={label}
      className="disabled-action"
      role="button"
      tabIndex={0}
    >
      {children}
      <span className="action-unavailable-note" id={reasonId} role="tooltip">
        Aun no disponible
      </span>
    </span>
  );
}
