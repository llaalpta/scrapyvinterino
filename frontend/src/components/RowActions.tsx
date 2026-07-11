import { ExternalLink, Heart, ShoppingCart } from 'lucide-react';
import type { Item } from '../api';

export function RowActions({ item }: { item: Item }) {
  return (
    <div className="row-actions">
      <a href={item.url} target="_blank" rel="noreferrer" title="Ver en Vinted">
        <ExternalLink size={17} />
      </a>
      <button type="button" title="Marcar favorito" disabled>
        <Heart size={17} />
      </button>
      <span className="disabled-action" title="La compra autenticada aun no esta disponible">
        <button aria-label="Compra autenticada aun no disponible" type="button" disabled>
          <ShoppingCart size={17} />
        </button>
      </span>
    </div>
  );
}
