"use client";

import { useEffect, useState } from "react";
import { OrderTile } from "@/components/store/ProductTile";
import { getOrderSuggestions } from "@/lib/api";
import { strings } from "@/lib/strings";
import type { GenderFilter, OrderSuggestions } from "@/lib/types";

const text = strings.orders;

/**
 * Catalogue products that go with the shopper's recent demo-store orders (the only purchases we know about).
 * Hidden while loading, when there are no orders, and when it can't be loaded: it's a bonus, never an error.
 */
export function FromYourOrders({ gender }: { gender: GenderFilter | null }) {
  const [shown, setShown] = useState<{ gender: GenderFilter | null; found: OrderSuggestions } | null>(null);

  useEffect(() => {
    let live = true;
    getOrderSuggestions(gender).then(
      (found) => live && setShown({ gender, found }),
      () => live && setShown(null),
    );
    return () => {
      live = false;
    };
  }, [gender]);

  const found = shown?.gender === gender ? shown.found : null;
  if (!found || found.items.length === 0) return null;

  return (
    <section aria-labelledby="from-orders-title" className="mb-6 flex flex-col gap-3">
      <h2 id="from-orders-title" className="display text-[1.5rem]">
        {text.railTitle}
      </h2>
      <ul className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-2">
        {found.items.map((pick) => (
          <li key={pick.product.id} className="w-40 flex-none sm:w-44" title={pick.reason || undefined}>
            <OrderTile item={pick.product} />
          </li>
        ))}
      </ul>
    </section>
  );
}
