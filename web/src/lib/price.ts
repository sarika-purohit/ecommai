import { rupees } from "@/lib/format";
import { strings } from "@/lib/strings";
import type { Cod, Price } from "@/lib/types";

interface Priced {
  total_price: number;
  price: Price | null;
  delivery_fee: number;
  cod: Cod | null;
}

/** The agent adds the COD fee to the total only when the checklist asks for cash on delivery. */
export function includesCodFee({ total_price, price, delivery_fee, cod }: Priced): boolean {
  return Boolean(price && cod?.available && cod.fee > 0 && total_price === price.selling + delivery_fee + cod.fee);
}

/** What the total includes, e.g. "incl. delivery and ₹29 cash-on-delivery fee". */
export function totalNote(item: Priced): string {
  return includesCodFee(item) && item.cod ? strings.card.inclDeliveryAndCod(rupees(item.cod.fee)) : strings.card.inclDelivery;
}

/** The COD fee when it isn't in the total yet, or that COD isn't offered. */
export function codNote(item: Priced): string | null {
  if (!item.cod || includesCodFee(item)) return null;
  if (!item.cod.available) return strings.card.noCod;
  return item.cod.fee > 0 ? strings.card.codExtra(rupees(item.cod.fee)) : null;
}
