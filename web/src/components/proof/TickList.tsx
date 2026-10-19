"use client";

import { useId, useState } from "react";
import { CheckIcon, ChevronDownIcon, CloseIcon, QuestionIcon } from "@/components/icons";
import { tickLabel } from "@/lib/labels";
import { strings } from "@/lib/strings";
import type { Evidence, ShoppingBrief, Status, Tick } from "@/lib/types";

const text = strings.proof;
const SHOP_DETAILS = new Set(["cod", "returnable", "exchange"]);

export function StatusIcon({ status, className }: { status: Status; className?: string }) {
  if (status === "pass") return <CheckIcon className={className} strokeWidth={3} />;
  if (status === "fail") return <CloseIcon className={className} strokeWidth={3} />;
  return <QuestionIcon className={className} strokeWidth={2.75} />;
}

interface TickListProps {
  ticks: Tick[];
  /** The checklist the ticks belong to, so they read the same as its chips. */
  brief: ShoppingBrief | null;
  /** A live store's product: proof comes from the store's photo and description, never demo reviews. */
  live?: boolean;
  store?: string | null;
  /** A page on another site, read by Gemini through Google: every proof is a quote from that page. */
  fromWeb?: boolean;
}

/** The tick-list: one tag per must-have. Tapping one opens its proof underneath. */
export function TickList({ ticks, brief, live = false, store = null, fromWeb = false }: TickListProps) {
  const [open, setOpen] = useState<string | null>(null);
  const proofId = useId();
  const openTick = ticks.find((tick) => tick.must_have_id === open) ?? null;
  const attributeOf = (tick: Tick) => brief?.must_haves.find((m) => m.id === tick.must_have_id)?.attribute ?? null;

  return (
    <div>
      <MatchSummary ticks={ticks} />
      <ul className="flex flex-wrap gap-2" aria-label={strings.card.mustHaves}>
        {ticks.map((tick) => {
          const label = tickLabel(tick, brief);
          const expanded = open === tick.must_have_id;
          return (
            <li key={tick.must_have_id} className="max-w-full">
              <button
                type="button"
                className="tag"
                data-tone={tick.status}
                data-icon=""
                aria-expanded={expanded}
                aria-controls={proofId}
                onClick={() => setOpen(expanded ? null : tick.must_have_id)}
              >
                <span className="tag-icon">
                  <StatusIcon status={tick.status} className="size-[1.125rem]" />
                </span>
                <span className="tag-text pr-1.5">
                  <span className="sr-only">{text.tick(label, strings.status[tick.status])}</span>
                  <span aria-hidden="true">{label}</span>
                </span>
                <ChevronDownIcon className="tag-chevron mr-2.5 size-4 flex-none self-center" />
              </button>
            </li>
          );
        })}
      </ul>
      <div id={proofId}>
        {openTick ? (
          <Proof
            key={openTick.must_have_id}
            tick={openTick}
            label={tickLabel(openTick, brief)}
            attribute={attributeOf(openTick)}
            live={live}
            store={store}
            fromWeb={fromWeb}
          />
        ) : null}
      </div>
    </div>
  );
}

/** Match % = must-haves proven ÷ must-haves asked for. "Not sure" counts as not proven, never as half. */
function MatchSummary({ ticks }: { ticks: Tick[] }) {
  if (ticks.length === 0) return null;
  const count = (status: Status) => ticks.filter((tick) => tick.status === status).length;
  const proven = count("pass");
  const parts = [text.matchWhy(proven, ticks.length)];
  if (count("unclear") > 0) parts.push(text.matchUnsure(count("unclear")));
  if (count("fail") > 0) parts.push(text.matchMissing(count("fail")));
  return (
    <p className="mb-2 tabular-nums">
      <span className="font-semibold">{text.match(Math.round((proven / ticks.length) * 100))}</span>
      <span className="text-ink-soft"> · {parts.join(" · ")}</span>
    </p>
  );
}

interface Origin {
  live: boolean;
  store: string | null;
  fromWeb: boolean;
}

/** Null when there's nothing to point at, e.g. a live store's general note on payment with no quote. */
function sourceLine(evidence: Evidence, attribute: string | null, { live, store, fromWeb }: Origin): string | null {
  if (fromWeb) return strings.web.proofSource;
  if (evidence.type === "image") return live ? text.fromStorePhoto : text.fromPhoto;
  if (evidence.type === "details" && live) return evidence.quote ? text.fromStoreDescription(store) : null;
  if (evidence.type === "details") return text.fromListing;
  if (evidence.type === "reviews") return text.fromReviews;
  return attribute && SHOP_DETAILS.has(attribute) ? text.fromStore : text.fromNoted;
}

interface ProofProps extends Origin {
  tick: Tick;
  label: string;
  attribute: string | null;
}

function Proof({ tick, label, attribute, live, store, fromWeb }: ProofProps) {
  const { evidence } = tick;
  const source = evidence ? sourceLine(evidence, attribute, { live, store, fromWeb }) : null;
  return (
    <section className="mt-3 rounded-2xl bg-paper p-4 ring-1 ring-line" aria-label={text.tick(label, strings.status[tick.status])}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <StatusPill status={tick.status} />
        <span className="font-semibold">{label}</span>
      </p>
      {evidence === null ? (
        <p className="mt-2">{text.nothing}</p>
      ) : (
        <>
          {evidence.note ? <p className="mt-2 leading-snug">{evidence.note}</p> : null}
          {evidence.type === "reviews" ? <ReviewProof evidence={evidence} demo={!live} /> : null}
          {evidence.type !== "reviews" && evidence.quote ? <Quote>{evidence.quote}</Quote> : null}
          {source ? <p className="mt-3 text-base text-ink-soft">{source}</p> : null}
        </>
      )}
    </section>
  );
}

export function StatusPill({ status }: { status: Status }) {
  const tone = { pass: "bg-pass-wash text-pass", fail: "bg-fail-wash text-fail", unclear: "bg-unclear-wash text-unclear" }[status];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-base font-semibold ${tone}`}>
      <StatusIcon status={status} className="size-4" />
      {strings.status[status]}
    </span>
  );
}

function Quote({ children }: { children: string }) {
  return (
    <blockquote className="mt-3 border-l-4 border-indigo pl-3 text-[1.125rem] italic leading-snug">
      “{children}”
    </blockquote>
  );
}

function ReviewProof({ evidence, demo }: { evidence: Evidence; demo: boolean }) {
  const agree = evidence.agree ?? 0;
  const disagree = evidence.disagree ?? 0;
  const total = agree + disagree;
  return (
    <div className="mt-3">
      {evidence.quote ? <Quote>{evidence.quote}</Quote> : null}
      {total > 0 ? (
        <div className="mt-3">
          <p className="font-semibold tabular-nums">{text.reviewCounts(agree, disagree)}</p>
          <div className="mt-1.5 flex h-2 overflow-hidden rounded-full bg-unclear-wash" aria-hidden="true">
            <span className="bg-pass" style={{ width: `${(agree / total) * 100}%` }} />
            <span className="bg-fail" style={{ width: `${(disagree / total) * 100}%` }} />
          </div>
        </div>
      ) : null}
      {demo ? (
        <p className="mt-3">
          <span className="demo-badge">{text.demoReviews}</span>
        </p>
      ) : null}
    </div>
  );
}
