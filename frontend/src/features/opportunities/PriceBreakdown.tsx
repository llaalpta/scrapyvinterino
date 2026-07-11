import type { Item } from '../../api';
import { formatMoney } from '../../utils/format';

export function PriceBreakdown({ item }: { item: Item }) {
  return (
    <div aria-label="Desglose de precio" className="price-breakdown">
      <PriceLine amount={item.price_amount} currency={item.currency} label="Articulo" />
      {item.buyer_protection_fee_amount ? (
        <PriceLine amount={item.buyer_protection_fee_amount} currency={item.currency} label="Proteccion" />
      ) : null}
      {item.total_price_amount ? <PriceLine amount={item.total_price_amount} currency={item.currency} emphasis label="Total sin envio" /> : null}
      {item.shipping_price_amount ? (
        <PriceLine amount={item.shipping_price_amount} currency={item.currency} label="Envio desde" />
      ) : null}
    </div>
  );
}

function PriceLine({
  amount,
  currency,
  emphasis = false,
  label
}: {
  amount: string | null;
  currency: string | null;
  emphasis?: boolean;
  label: string;
}) {
  return (
    <span className={emphasis ? 'price-line total' : 'price-line'}>
      <span>{label}</span>
      <strong>{formatMoney(amount, currency)}</strong>
    </span>
  );
}
