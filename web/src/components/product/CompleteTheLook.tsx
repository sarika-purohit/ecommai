"use client";

import { useEffect, useState } from "react";
import { LookTile } from "@/components/store/ProductTile";
import { getLooks } from "@/lib/api";
import { canRetry, errorMessage } from "@/lib/errors";
import { rupees } from "@/lib/format";
import { strings } from "@/lib/strings";
import type { GenderFilter, Look, LookPartName, LookPiece, Looks, LookSlot, LookStyle, LookSuggestion, Occasion, ProductDetails } from "@/lib/types";

const text = strings.look;
const OCCASIONS = Object.keys(text.occasions) as Occasion[];
const STYLES = Object.keys(text.styles) as LookStyle[];

type Load = { status: "loading" } | { status: "ready"; looks: Looks } | { status: "error"; message: string; retryable: boolean };

interface Filters {
  occasion: Occasion | null;
  budget: number | null;
  style: LookStyle | null;
}

/**
 * Gemini's outfit ideas around a sample-catalogue product: every pick is a real catalogue product
 * with its match explained; the finishing touches are ideas, never products.
 */
export function CompleteTheLook({ product, gender }: { product: ProductDetails; gender: GenderFilter | null }) {
  const [filters, setFilters] = useState<Filters>({ occasion: null, budget: null, style: null });
  const [attempt, setAttempt] = useState(0);
  const change = (next: Partial<Filters>) => setFilters((current) => ({ ...current, ...next }));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Chips
          legend={text.occasionLegend}
          options={OCCASIONS.map((occasion) => ({ value: occasion, label: text.occasions[occasion] }))}
          value={filters.occasion}
          onChange={(occasion) => change({ occasion })}
        />
        <Chips
          legend={text.budgetLegend}
          options={text.budgets.map((amount) => ({ value: amount, label: rupees(amount) }))}
          value={filters.budget}
          onChange={(budget) => change({ budget })}
        />
        <Chips
          legend={text.styleLegend}
          options={STYLES.map((style) => ({ value: style, label: text.styles[style] }))}
          value={filters.style}
          onChange={(style) => change({ style })}
        />
      </div>
      <LooksLoader
        key={`${filters.occasion}:${filters.budget}:${filters.style}:${attempt}`}
        product={product}
        gender={product.gender === "unisex" ? gender : null}
        filters={filters}
        onRetry={() => setAttempt((n) => n + 1)}
      />
    </div>
  );
}

interface ChipsProps<T extends string | number> {
  legend: string;
  options: { value: T; label: string }[];
  value: T | null;
  onChange: (value: T | null) => void;
}

function Chips<T extends string | number>({ legend, options, value, onChange }: ChipsProps<T>) {
  return (
    <div role="group" aria-label={legend} className="-mx-1 flex items-center gap-2 overflow-x-auto px-1 pb-1">
      <span aria-hidden="true" className="flex-none text-base text-ink-soft">
        {legend}
      </span>
      <button type="button" className="chip" aria-pressed={value === null} onClick={() => onChange(null)}>
        {text.any}
      </button>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className="chip"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

interface LooksLoaderProps {
  product: ProductDetails;
  gender: GenderFilter | null;
  filters: Filters;
  onRetry: () => void;
}

function LooksLoader({ product, gender, filters, onRetry }: LooksLoaderProps) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const { occasion, budget, style } = filters;

  useEffect(() => {
    let live = true;
    getLooks(product.id, {
      ...(occasion ? { occasion } : {}),
      ...(style ? { style } : {}),
      ...(budget ? { budget_max_inr: budget } : {}),
      ...(gender ? { gender } : {}),
    }).then(
      (looks) => live && setLoad({ status: "ready", looks }),
      (error: unknown) => live && setLoad({ status: "error", message: errorMessage(error), retryable: canRetry(error) }),
    );
    return () => {
      live = false;
    };
  }, [product.id, occasion, style, budget, gender]);

  if (load.status === "loading") {
    return (
      <p role="status" className="text-ink-soft">
        {text.loading}
      </p>
    );
  }
  if (load.status === "error") {
    return (
      <div role="alert" className="panel p-4">
        <p className="font-semibold text-fail">{text.failed}</p>
        <p className="text-base text-ink-soft">{load.message}</p>
        {load.retryable ? (
          <button type="button" className="btn btn-primary btn-small mt-3" onClick={onRetry}>
            {strings.common.tryAgain}
          </button>
        ) : null}
      </div>
    );
  }

  const { looks } = load;
  const anchorPrice = product.price?.selling ?? null;
  return (
    <div className="flex flex-col gap-5">
      {looks.note ? <p className="text-ink-soft">{looks.note}</p> : null}
      {looks.looks.map((look, index) => (
        <LookCard key={`${look.occasion}:${index}`} look={look} anchorPrice={anchorPrice} budget={looks.budget_max_inr} />
      ))}
      {looks.rows.map((row) =>
        row.items.length > 0 ? (
          <section key={row.kind} aria-label={text.rows[row.kind]}>
            <h3 className="font-semibold">{text.rows[row.kind]}</h3>
            <ul className="-mx-1 mt-2 flex gap-3 overflow-x-auto px-1 pb-2">
              {row.items.map((suggestion) => (
                <li key={suggestion.product.id} className="w-40 flex-none sm:w-44">
                  <LookTile suggestion={suggestion} />
                </li>
              ))}
            </ul>
          </section>
        ) : null,
      )}
      <Footer looks={looks} />
    </div>
  );
}

interface LookCardProps {
  look: Look;
  anchorPrice: number | null;
  budget: number | null;
}

function LookCard({ look, anchorPrice, budget }: LookCardProps) {
  const [chosen, setChosen] = useState<Partial<Record<LookSlot, number>>>({});
  const [removed, setRemoved] = useState<LookSlot[]>([]);
  const shown = look.pieces.filter((piece) => !removed.includes(piece.slot));
  const picks = shown.map((piece) => piece.choices[(chosen[piece.slot] ?? 0) % piece.choices.length]);
  const total = lookTotal(anchorPrice, picks);

  return (
    <article className="panel flex flex-col gap-3 p-4">
      <div>
        <p className="flex flex-wrap items-center gap-2">
          <span className="eyebrow">{text.occasions[look.occasion]}</span>
          <span className="demo-badge">{text.suggestion}</span>
        </p>
        <h3 className="display mt-1 text-[1.5rem]">{look.title}</h3>
        {look.summary ? <p className="mt-1 leading-snug text-ink-soft">{look.summary}</p> : null}
      </div>

      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {shown.map((piece, index) => (
          <PieceItem
            key={piece.slot}
            piece={piece}
            pick={picks[index]}
            onSwap={() => setChosen((current) => ({ ...current, [piece.slot]: (current[piece.slot] ?? 0) + 1 }))}
            onRemove={() => setRemoved((current) => [...current, piece.slot])}
          />
        ))}
      </ul>
      {removed.length > 0 ? (
        <button type="button" className="link self-start" onClick={() => setRemoved([])}>
          {text.reset}
        </button>
      ) : null}

      {look.ideas.length > 0 ? (
        <div>
          <p className="text-base font-semibold">{text.ideasTitle}</p>
          <ul className="mt-1 list-disc pl-5 text-base text-ink-soft">
            {look.ideas.map((idea) => (
              <li key={idea}>{idea}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="tabular-nums">
        {total === null ? (
          <p className="text-ink-soft">{text.noTotal}</p>
        ) : (
          <>
            <p className="font-semibold">{text.total(rupees(total))}</p>
            <p className="text-base text-ink-soft">{text.totalNote}</p>
          </>
        )}
        {total !== null && budget !== null && total > budget ? (
          <p className="field-error mt-1">{text.overBudget(rupees(budget))}</p>
        ) : null}
        {look.left_out.length > 0 && budget !== null ? (
          <p className="mt-1 text-base text-ink-soft">
            {text.leftOut(look.left_out.map((slot) => text.slots[slot].toLowerCase()).join(", "), rupees(budget))}
          </p>
        ) : null}
      </div>
    </article>
  );
}

/** The product plus the picks shown; no total at all when any price is missing. */
function lookTotal(anchorPrice: number | null, picks: LookSuggestion[]): number | null {
  if (anchorPrice === null) return null;
  let total = anchorPrice;
  for (const pick of picks) {
    if (!pick.product.price) return null;
    total += pick.product.price.selling;
  }
  return total;
}

interface PieceItemProps {
  piece: LookPiece;
  pick: LookSuggestion;
  onSwap: () => void;
  onRemove: () => void;
}

function PieceItem({ piece, pick, onSwap, onRemove }: PieceItemProps) {
  const slot = text.slots[piece.slot];
  return (
    <li className="flex flex-col gap-1.5">
      <p className="text-base font-semibold text-ink-soft">{slot}</p>
      <LookTile suggestion={pick} />
      <div className="flex flex-wrap gap-x-3">
        {piece.choices.length > 1 ? (
          <button type="button" className="link" aria-label={text.swapLabel(slot)} onClick={onSwap}>
            {text.swap}
          </button>
        ) : null}
        <button type="button" className="link" aria-label={text.removeLabel(slot)} onClick={onRemove}>
          {text.remove}
        </button>
      </div>
      <details className="text-base">
        <summary className="cursor-pointer font-semibold">{text.why}</summary>
        <dl className="mt-1 flex flex-col gap-0.5 tabular-nums">
          {pick.parts.map((part) => (
            <div key={part.name} className="flex justify-between gap-2">
              <dt className="text-ink-soft">{text.parts[part.name]}</dt>
              <dd className={part.score === null ? "text-ink-soft" : undefined}>{text.partScore(part.score, part.weight)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </li>
  );
}

function Footer({ looks }: { looks: Looks }) {
  const weights = (Object.entries(looks.weights) as [LookPartName, number][])
    .map(([name, weight]) => `${text.parts[name].toLowerCase()} ${weight}`)
    .join(", ");
  return (
    <div className="flex flex-col gap-2 text-base text-ink-soft">
      <p>
        <span className="font-semibold text-ink">{text.howTitle}: </span>
        {text.how(weights)}
      </p>
      {looks.sample_data || looks.demo_prices ? (
        <p className="flex flex-wrap gap-2">
          {looks.sample_data ? <span className="demo-badge">{text.sample}</span> : null}
          {looks.demo_prices ? <span className="demo-badge">{text.demoPrices}</span> : null}
        </p>
      ) : null}
    </div>
  );
}
