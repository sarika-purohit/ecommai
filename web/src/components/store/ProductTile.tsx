"use client";

import Image from "next/image";
import Link from "next/link";
import { StarIcon, StoreIcon } from "@/components/icons";
import { StatusIcon } from "@/components/proof/TickList";
import { rupees } from "@/lib/format";
import { tickLabel } from "@/lib/labels";
import { strings } from "@/lib/strings";
import type { CatalogItem, LookSuggestion, Price, ResultCard, ShoppingBrief } from "@/lib/types";

const text = strings.store;
const MAX_TICKS = 3;

export function productHref(id: string): string {
  return `/product/${encodeURIComponent(id)}`;
}

/** A demo catalogue product on a store page: photo, name, price, rating. */
export function ProductTile({ item }: { item: CatalogItem }) {
  return (
    <TileFrame href={productHref(item.id)} photo={item.thumb ?? item.photo} title={item.title} brand={item.brand}>
      <PriceLine price={item.price} />
      <Rating rating={item.rating_avg} count={item.rating_count} />
    </TileFrame>
  );
}

/** A product that goes with a recent order: photo, name and price only. */
export function OrderTile({ item }: { item: CatalogItem }) {
  return (
    <TileFrame href={productHref(item.id)} photo={item.thumb ?? item.photo} title={item.title} brand={item.brand}>
      <PriceLine price={item.price} />
    </TileFrame>
  );
}

/** A product the AI checked: the store tile plus the first ticks of its checklist. */
export function AiTile({ card, brief }: { card: ResultCard; brief: ShoppingBrief }) {
  const shown = card.ticks.slice(0, MAX_TICKS);
  const more = card.ticks.length - shown.length;
  return (
    <TileFrame href={productHref(card.product_id)} photo={card.thumb ?? card.photo} title={card.title} brand={card.brand}>
      <p className="display mt-1 text-[1.25rem] tabular-nums">{rupees(card.total_price)}</p>
      {card.source === "shopify" && card.store ? (
        <p className="flex items-center gap-1 text-[0.9375rem] leading-snug text-ink-soft">
          <StoreIcon className="size-3.5 flex-none text-indigo" />
          <span className="truncate">{strings.card.fromStore(card.store)}</span>
        </p>
      ) : null}
      {shown.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1">
          {shown.map((tick) => {
            const label = tickLabel(tick, brief);
            return (
              <li key={tick.must_have_id} className="flex items-start gap-1.5 text-[0.9375rem] leading-snug">
                <span className="verdict-mini" data-state={tick.status} aria-hidden="true">
                  <StatusIcon status={tick.status} className="size-3" />
                </span>
                <span className="sr-only">{strings.proof.tick(label, strings.status[tick.status])}</span>
                <span aria-hidden="true" className="line-clamp-1">
                  {label}
                </span>
              </li>
            );
          })}
          {more > 0 ? <li className="text-[0.9375rem] text-ink-soft">{text.moreTicks(more)}</li> : null}
        </ul>
      ) : null}
      <Rating rating={card.rating_avg} count={card.rating_count} />
    </TileFrame>
  );
}

/** A product suggested to complete a look: the store tile plus its match and Gemini's styling note. */
export function LookTile({ suggestion }: { suggestion: LookSuggestion }) {
  const { product } = suggestion;
  const look = strings.look;
  return (
    <TileFrame href={productHref(product.id)} photo={product.thumb ?? product.photo} title={product.title} brand={product.brand}>
      <PriceLine price={product.price} />
      <p className="mt-1 text-[0.9375rem] font-semibold tabular-nums">
        {suggestion.match === null ? <span className="text-ink-soft">{look.unscored}</span> : look.match(suggestion.match)}
      </p>
      {suggestion.reason ? <p className="mt-0.5 line-clamp-2 text-[0.875rem] leading-snug text-ink-soft">{suggestion.reason}</p> : null}
    </TileFrame>
  );
}

interface TileFrameProps {
  href: string;
  photo: string | null;
  title: string;
  brand: string | null;
  children: React.ReactNode;
}

function TileFrame({ href, photo, title, brand, children }: TileFrameProps) {
  return (
    <article className="group relative flex h-full flex-col overflow-hidden rounded-2xl bg-card ring-1 ring-line transition hover:ring-2 hover:ring-indigo">
      <div className="relative aspect-[3/4] bg-white">
        {photo ? (
          <Image
            src={photo}
            alt=""
            fill
            unoptimized
            sizes="(min-width: 1024px) 20vw, (min-width: 640px) 33vw, 50vw"
            className="object-contain p-2"
          />
        ) : (
          <span className="block h-full bg-wash" />
        )}
      </div>
      <div className="flex flex-1 flex-col p-3">
        {brand ? <p className="eyebrow truncate text-[0.75rem] leading-tight">{brand}</p> : null}
        <h3 className="mt-0.5 line-clamp-2 text-base font-semibold leading-snug">
          <Link href={href} className="after:absolute after:inset-0 focus:outline-none">
            {title}
          </Link>
        </h3>
        {children}
      </div>
    </article>
  );
}

function PriceLine({ price }: { price: Price | null }) {
  if (!price) return null;
  const off = price.mrp > price.selling ? Math.round(((price.mrp - price.selling) / price.mrp) * 100) : 0;
  return (
    <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5">
      <span className="display text-[1.25rem] tabular-nums">{rupees(price.selling)}</span>
      {off > 0 ? (
        <>
          <s className="text-[0.9375rem] text-ink-soft tabular-nums">{rupees(price.mrp)}</s>
          <span className="text-[0.9375rem] font-semibold text-pass">{text.off(off)}</span>
        </>
      ) : null}
    </p>
  );
}

function Rating({ rating, count }: { rating: number | null; count: number }) {
  if (rating === null || count === 0) return null;
  return (
    <p className="mt-auto pt-1.5" aria-label={text.ratingLabel(rating, count)}>
      <span className="inline-flex items-center gap-1 rounded-md bg-pass px-1.5 py-0.5 text-[0.8125rem] font-semibold text-white tabular-nums">
        {text.rating(rating)}
        <StarIcon className="size-3" fill="currentColor" strokeWidth={0} />
      </span>
      <span className="ml-1.5 text-[0.875rem] text-ink-soft tabular-nums">({count})</span>
    </p>
  );
}
