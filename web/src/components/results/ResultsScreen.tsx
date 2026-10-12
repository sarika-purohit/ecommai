"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useReducer, useRef, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CheckIcon, CloseIcon, QuestionIcon } from "@/components/icons";
import { NoSession, ScreenPlaceholder } from "@/components/NoSession";
import { isAbort, searchProducts } from "@/lib/api";
import { canRetry, errorMessage } from "@/lib/errors";
import { tradeoffLabel } from "@/lib/labels";
import { sessionStore } from "@/lib/session";
import { useIsClient } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { DoneEvent, SearchEvent, Status } from "@/lib/types";

const text = strings.results;

type SlotState = "checking" | Status;

interface Progress {
  found: number | null;
  slots: { productId: string; state: SlotState }[];
  done: DoneEvent | null;
  error: { message: string; retryable: boolean } | null;
}

type Action = { type: "event"; event: SearchEvent } | { type: "failed"; message: string; retryable: boolean };

const START: Progress = { found: null, slots: [], done: null, error: null };

function reduce(state: Progress, action: Action): Progress {
  if (action.type === "failed") return { ...state, error: { message: action.message, retryable: action.retryable } };
  const { event } = action;
  switch (event.type) {
    case "candidates":
      return { ...START, found: event.count };
    case "checking":
      if (state.slots.some((slot) => slot.productId === event.product_id)) return state;
      return { ...state, slots: [...state.slots, { productId: event.product_id, state: "checking" }] };
    case "verified": {
      const known = state.slots.some((slot) => slot.productId === event.product_id);
      const slots = known
        ? state.slots.map((slot) => (slot.productId === event.product_id ? { ...slot, state: event.status } : slot))
        : [...state.slots, { productId: event.product_id, state: event.status }];
      return { ...state, slots };
    }
    case "done":
      return { ...state, done: event };
    default:
      return state;
  }
}

export function ResultsScreen() {
  const isClient = useIsClient();
  const session = sessionStore.use();
  const [retries, setRetries] = useState(0);

  return (
    <>
      <AppHeader back={{ href: "/understood", label: text.back }} />
      <main className="shell pb-12 pt-2">
        {!isClient ? (
          <ScreenPlaceholder />
        ) : session ? (
          <SearchProgress
            key={`${session.sessionId}:${session.searchRun}:${retries}`}
            sessionId={session.sessionId}
            onRetry={() => setRetries((count) => count + 1)}
          />
        ) : (
          <NoSession />
        )}
      </main>
    </>
  );
}

function SearchProgress({ sessionId, onRetry }: { sessionId: string; onRetry: () => void }) {
  const router = useRouter();
  const [progress, dispatch] = useReducer(reduce, START);
  const finished = useRef(false);

  useEffect(() => {
    if (finished.current) return;
    const controller = new AbortController();
    (async () => {
      try {
        for await (const event of searchProducts(sessionId, controller.signal)) {
          if (event.type === "done") finished.current = true;
          dispatch({ type: "event", event });
        }
      } catch (error) {
        if (isAbort(error)) return;
        finished.current = true;
        dispatch({ type: "failed", message: errorMessage(error), retryable: canRetry(error) });
      }
    })();
    return () => controller.abort();
  }, [sessionId]);

  const checking = progress.slots.length;
  const passed = progress.slots.filter((slot) => slot.state === "pass").length;
  const { done, error } = progress;

  function startOver() {
    sessionStore.set(null);
    router.push("/");
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display text-[2.25rem] sm:text-[2.75rem]">{done ? text.doneTitle : text.title}</h1>
        <p className="display mt-3 text-[1.5rem] font-semibold tabular-nums text-indigo" aria-hidden="true">
          {progress.found === null ? text.starting : text.progress(progress.found, checking, done ? done.passed : passed)}
        </p>
        <p role="status" aria-live="polite" className="sr-only">
          {done ? text.summary(done.checked, done.passed) : progress.found !== null ? text.liveFound(progress.found) : text.starting}
        </p>
      </div>

      {progress.slots.length > 0 ? (
        <ul aria-label={text.slotsLabel} className="grid grid-cols-4 gap-2">
          {progress.slots.map((slot, index) => (
            <li
              key={slot.productId}
              className="slot flex h-14 items-center justify-center rounded-xl"
              data-state={slot.state}
            >
              <span className="sr-only">{text.slotLabel(index + 1, text.slot[slot.state])}</span>
              <SlotMark state={slot.state} />
            </li>
          ))}
        </ul>
      ) : null}

      {error ? (
        <div role="alert" className="panel p-5">
          <p className="font-semibold text-fail">{error.message}</p>
          {error.retryable ? (
            <button type="button" className="btn btn-primary btn-small mt-4" onClick={onRetry}>
              {strings.common.tryAgain}
            </button>
          ) : null}
        </div>
      ) : null}

      {done ? <Summary done={done} /> : null}

      {done || error ? (
        <div className="flex flex-col gap-3">
          <Link href="/understood" className="btn btn-secondary w-full">
            {text.back}
          </Link>
          <button type="button" className="btn btn-secondary w-full" onClick={startOver}>
            {text.newSearch}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function SlotMark({ state }: { state: SlotState }) {
  if (state === "pass") return <CheckIcon className="size-7" strokeWidth={3} />;
  if (state === "fail") return <CloseIcon className="size-6" strokeWidth={2.5} />;
  if (state === "unclear") return <QuestionIcon className="size-7" strokeWidth={2.5} />;
  return <span className="slot-dot size-3 rounded-full bg-indigo" aria-hidden="true" />;
}

function Summary({ done }: { done: DoneEvent }) {
  const rejected = Object.entries(done.rejected);
  return (
    <section className="flex flex-col gap-5">
      <p className="text-[1.25rem] font-semibold">{text.summary(done.checked, done.passed)}</p>
      {done.unclear > 0 ? <p className="text-ink-soft">{text.unclear(done.unclear)}</p> : null}

      {done.tradeoffs.length > 0 ? (
        <div>
          <h2 className="eyebrow">{text.tradeoffsTitle}</h2>
          <ul className="mt-2 flex flex-col gap-2">
            {done.tradeoffs.map((tradeoff) => (
              <li key={tradeoff.relax} className="panel flex items-center justify-between gap-3 p-4">
                <span className="font-semibold">{tradeoffLabel(tradeoff)}</span>
                <span className="tabular-nums text-ink-soft">{text.tradeoffMatches(tradeoff.matches)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {rejected.length > 0 ? (
        <div>
          <hr className="stitch" />
          <h2 className="eyebrow pt-4">{text.rejectedTitle}</h2>
          <dl className="mt-2 divide-y divide-line">
            {rejected.map(([reason, count]) => (
              <div key={reason} className="flex items-baseline justify-between gap-4 py-2.5">
                <dt className="capitalize-first">{reason}</dt>
                <dd className="font-display text-[1.25rem] font-semibold tabular-nums">{count}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </section>
  );
}
