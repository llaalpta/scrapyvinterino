import type { Item } from '../api';

export function ItemCell({ item }: { item: Item }) {
  return (
    <div className="item-cell">
      {item.image_url ? <img src={item.image_url} alt="" /> : <div className="thumb" />}
      <span>{item.title}</span>
    </div>
  );
}
