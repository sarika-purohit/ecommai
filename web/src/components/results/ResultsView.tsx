"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { ArrowRightIcon, ChevronDownIcon, InfoIcon, StoreIcon } from "@/components/icons";
import { OtherSites } from "@/components/results/OtherSites";
import { ResultCardView } from "@/components/results/ResultCardView";
import { updateBrief } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { tradeoffLabel } from "@/lib/labels";
import {
  clearSearch,
  sameRule,
  sourceOf,
  sourceStore,
  tradeoffChanges,
  updateSession,
  type SearchSession,
  type StoredResults,
} from "@/lib/session";
import { strings } from "@/lib/strings";
import type { BriefChange, DoneEvent, SearchSource, ShoppingBrief } from "@/lib/types";

const text = strings.results;

export function resultsSummary(done: DoneEvent, source: SearchSource): string {
  return done.checked === 0 ? text.nothingFound[source] : text.summary(done.checked, done.passed);
}

interface ResultsViewProps {
  session: SearchSession;
  results: StoredResults;
  /** True right after the check finishes here, so focus moves from the progress to the results. */
  focusHeading: boolean;
  onAnnounce: (message: string) => void;
}

export function ResultsView({ session, results, focusHeading, onAnnounce }: ResultsViewProps) {
  const router = useRouter();
  const heading = useRef<HTMLHeadingElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { done, brief } = results;
  const nothing = done.passed === 0;
  const source = results.source ?? sourceOf(session);
  const otherSource: SearchSource = source === "live" ? "catalog" : "live";

  useEffect(() => {
    if (focusHeading) heading.current?.focus();
  }, [focusHeading]);

  /** Applies checklist edits in order, then starts a fresh search with the new checklist. */
  async function refine(key: string, changes: BriefChange[]) {
    if (changes.length === 0) return;
    setBusy(key);
    setError(null);
    onAnnounce(text.refining);
    try {
      let updated = session.brief;
      for (const change of changes) ({ brief: updated } = await updateBrief(session.sessionId, change));
      const switchedOff = changes.flatMap((c) => (c.type === "set_rule" && !c.enabled ? [c.must_have] : []));
      const newSource = changes.flatMap((c) => (c.type === "set_source" ? [c.source] : [])).pop();
      updateSession((current) => ({
        ...current,
        brief: updated,
        source: newSource ?? current.source,
        rulesOff: [...current.rulesOff.filter((off) => !switchedOff.some((m) => sameRule(m, off))), ...switchedOff],
        searchRun: current.searchRun + 1,
      }));
      if (newSource) sourceStore.set(newSource);
      window.scrollTo({ top: 0 });
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(null);
    }
  }

  function startOver() {
    clearSearch();
    router.push("/");
  }

  return (
    <div className="flex flex-col gap-6" aria-busy={busy !== null}>
      <div>
        <h1 ref={heading} tabIndex={-1} className="display text-[2.25rem] focus:outline-none sm:text-[2.75rem]">
          {nothing ? text.noMatchesTitle : text.doneTitle}
        </h1>
        <p className="mt-2 text-[1.25rem] font-semibold">{resultsSummary(done, source)}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-ink-soft">
          <StoreIcon className="size-5 flex-none text-indigo" />
          <span>{strings.source.searched[source]}</span>
          <button
            type="button"
            className="link"
            disabled={busy !== null}
            aria-busy={busy === "source"}
            onClick={() => void refine("source", [{ type: "set_source", source: otherSource }])}
          >
            {strings.source.switchTo[otherSource]}
          </button>
        </p>
      </div>

      {done.not_checkable.length > 0 ? (
        <div className="flex gap-3 rounded-2xl bg-wash p-4">
          <InfoIcon className="mt-0.5 size-5 flex-none text-indigo" />
          <div>
            <p className="font-semibold">{text.notCheckable(done.not_checkable)}</p>
            <p className="text-base text-ink-soft">{text.notCheckableHint}</p>
          </div>
        </div>
      ) : null}

      {busy ? (
        <p className="font-semibold text-indigo" aria-hidden="true">
          {text.refining}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="field-error">
          {error}
        </p>
      ) : null}

      {nothing ? <Tradeoffs done={done} brief={brief} busy={busy} onPick={refine} /> : null}

      {done.matches.length > 0 ? (
        <section className="flex flex-col gap-3" aria-label={text.doneTitle}>
          <p className="text-base text-ink-soft">{text.proofHint}</p>
          <ul className="flex flex-col gap-4">
            {done.matches.map((card) => (
              <li key={card.product_id}>
                <ResultCardView card={card} brief={brief} sessionId={session.sessionId} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {nothing ? null : <Tradeoffs done={done} brief={brief} busy={busy} onPick={refine} />}

      <Refine busy={busy} onPick={(label, change) => void refine(label, [change])} />

      {done.almost.length > 0 ? <Almost done={done} brief={brief} sessionId={session.sessionId} /> : null}

      <Rejected done={done} />

      {results.web ? <OtherSites web={results.web} /> : null}

      <div className="flex flex-col gap-3">
        <Link href="/understood" className="btn btn-secondary w-full">
          {text.back}
        </Link>
        <button type="button" className="btn btn-secondary w-full" onClick={startOver}>
          {text.newSearch}
        </button>
      </div>
    </div>
  );
}

interface TradeoffsProps {
  done: DoneEvent;
  brief: ShoppingBrief;
  busy: string | null;
  onPick: (key: string, changes: BriefChange[]) => Promise<void>;
}

function Tradeoffs({ done, brief, busy, onPick }: TradeoffsProps) {
  const titleId = useId();
  if (done.tradeoffs.length === 0) {
    return done.passed === 0 && done.checked > 0 ? <p className="panel p-5 text-ink-soft">{text.noTradeoffs}</p> : null;
  }
  return (
    <section aria-labelledby={titleId} className="rounded-[1.25rem] bg-marigold-wash p-5 ring-2 ring-marigold">
      <h2 id={titleId} className="eyebrow text-ink">
        {text.tradeoffsTitle}
      </h2>
      <p className="mt-1">{done.passed === 0 ? text.tradeoffsNone : text.tradeoffsFew}</p>
      <ul className="mt-3 flex flex-col gap-2">
        {done.tradeoffs.map((tradeoff) => (
          <li key={tradeoff.relax}>
            <button
              type="button"
              className="flex min-h-16 w-full items-center gap-3 rounded-2xl bg-card px-4 py-3 text-left ring-1 ring-line transition hover:ring-2 hover:ring-indigo disabled:opacity-60"
              disabled={busy !== null}
              onClick={() => void onPick(tradeoff.relax, tradeoffChanges(tradeoff, brief))}
            >
              <span className="min-w-0 flex-1">
                <span className="block text-[1.125rem] font-semibold leading-snug">{tradeoffLabel(tradeoff, brief)}</span>
                <span className="block text-base text-ink-soft tabular-nums">{text.tradeoffMatches(tradeoff.matches)}</span>
              </span>
              {busy === tradeoff.relax ? (
                <span className="size-3 flex-none animate-pulse rounded-full bg-indigo" aria-hidden="true" />
              ) : (
                <ArrowRightIcon className="size-5 flex-none text-indigo" />
              )}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Refine({ busy, onPick }: { busy: string | null; onPick: (label: string, change: BriefChange) => void }) {
  const titleId = useId();
  return (
    <section aria-labelledby={titleId}>
      <hr className="stitch" />
      <h2 id={titleId} className="eyebrow pt-4">
        {text.refineTitle}
      </h2>
      <ul className="mt-3 flex flex-wrap gap-2">
        {text.refine.map((option) => (
          <li key={option.label}>
            <button
              type="button"
              className="btn btn-secondary btn-small rounded-full"
              disabled={busy !== null}
              aria-busy={busy === option.label}
              onClick={() => onPick(option.label, { type: "text", text: option.text })}
            >
              {option.label}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Almost({ done, brief, sessionId }: { done: DoneEvent; brief: ShoppingBrief; sessionId: string }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  return (
    <section>
      <hr className="stitch" />
      <button
        type="button"
        className="mt-3 flex min-h-12 w-full items-center justify-between gap-3 text-left font-display text-[1.25rem] font-semibold"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((value) => !value)}
      >
        {text.unclear(done.almost.length)}
        <ChevronDownIcon className={`size-5 flex-none text-indigo transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      <div id={listId} hidden={!open}>
        <p className="mt-1 text-base text-ink-soft">{text.unclearHint}</p>
        <ul className="mt-3 flex flex-col gap-4">
          {done.almost.map((card) => (
            <li key={card.product_id}>
              <ResultCardView card={card} brief={brief} sessionId={sessionId} />
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Rejected({ done }: { done: DoneEvent }) {
  const titleId = useId();
  const rows: [string, number][] = Object.entries(done.rejected);
  if (done.duplicates > 0) rows.push([text.duplicates(done.duplicates), done.duplicates]);
  if (rows.length === 0) return null;
  return (
    <section aria-labelledby={titleId}>
      <hr className="stitch" />
      <h2 id={titleId} className="eyebrow pt-4">
        {text.rejectedTitle}
      </h2>
      <dl className="mt-2 divide-y divide-line">
        {rows.map(([reason, count]) => (
          <div key={reason} className="flex items-baseline justify-between gap-4 py-2.5">
            <dt className="capitalize-first">{reason}</dt>
            <dd className="font-display text-[1.25rem] font-semibold tabular-nums">{count}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
