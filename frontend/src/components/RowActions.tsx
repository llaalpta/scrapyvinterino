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
      <button type="button" title="Comprar" disabled>
        <ShoppingCart size={17} />
      </button>
    </div>
  );
}
