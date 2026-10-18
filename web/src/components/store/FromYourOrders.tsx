"use client";

import { useEffect, useState } from "react";
import { LookTile } from "@/components/store/ProductTile";
import { getOrderSuggestions } from "@/lib/api";
import { shortDayInIndia } from "@/lib/format";
import { strings } from "@/lib/strings";
import type { GenderFilter, OrderSuggestions } from "@/lib/types";

const text = strings.orders;
/** Each tile names at most this many orders it goes with. */
const MAX_NAMED = 2;

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
  const ideas = [...new Set(found.ideas.map((idea) => idea.text))];

  return (
    <section aria-labelledby="from-orders-title" className="mb-6 flex flex-col gap-3">
      <div>
        <h2 id="from-orders-title" className="display text-[1.5rem]">
          {text.railTitle}
        </h2>
        <p className="text-base text-ink-soft">{text.railHint}</p>
      </div>
      <ul className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-2">
        {found.items.map((pick) => (
          <li key={pick.product.id} className="flex w-40 flex-none flex-col gap-1.5 sm:w-44">
            <LookTile suggestion={{ slot: pick.slot, product: pick.product, match: pick.match, parts: [], reason: pick.reason }} />
            {pick.goes_with.slice(0, MAX_NAMED).map((order) => (
              <p key={order.product_id} className="text-[0.875rem] leading-snug text-ink-soft">
                {text.goesWith(order.title, shortDayInIndia(order.ordered_at), order.seeded)}
              </p>
            ))}
          </li>
        ))}
      </ul>
      {ideas.length > 0 ? <p className="text-base text-ink-soft">{text.ideas(ideas.join(", "))}</p> : null}
    </section>
  );
}
