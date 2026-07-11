import type { Item } from '../api';
import { ItemPhoto } from './ItemPhoto';

export function ItemCell({ item, onOpenDetails }: { item: Item; onOpenDetails?: () => void }) {
  const photo = item.photos.find(Boolean) ?? item.image_url;
  const content = (
    <>
      <ItemPhoto alt="" className="item-thumbnail" src={photo} />
      <span>{item.title}</span>
    </>
  );

  return (
    <div className="item-cell">
      {onOpenDetails ? (
        <button aria-label={`Ver detalle y fotos de ${item.title}`} className="item-cell-trigger" type="button" onClick={onOpenDetails}>
          {content}
        </button>
      ) : (
        content
      )}
    </div>
  );
}
