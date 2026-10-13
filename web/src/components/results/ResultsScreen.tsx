"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useEffectEvent, useReducer, useRef, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { PhotoIcon } from "@/components/icons";
import { NoSession, ScreenPlaceholder } from "@/components/NoSession";
import { StatusIcon } from "@/components/proof/TickList";
import { ResultsView } from "@/components/results/ResultsView";
import { ApiError, isAbort, searchProducts } from "@/lib/api";
import { canRetry, errorMessage } from "@/lib/errors";
import { clearSearch, resultsStore, sessionStore, type SearchSession } from "@/lib/session";
import { useIsClient } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { DoneEvent, SearchEvent, Status } from "@/lib/types";

const text = strings.results;

type SlotState = "checking" | Status;

interface Slot {
  productId: string;
  title: string;
  thumb: string | null;
  state: SlotState;
}

interface Progress {
  found: number | null;
  dropped: number;
  slots: Slot[];
  error: { message: string; retryable: boolean } | null;
}

type Action = { type: "event"; event: SearchEvent } | { type: "failed"; message: string; retryable: boolean };

const START: Progress = { found: null, dropped: 0, slots: [], error: null };

function reduce(state: Progress, action: Action): Progress {
  if (action.type === "failed") return { ...state, error: { message: action.message, retryable: action.retryable } };
  const { event } = action;
  switch (event.type) {
    case "candidates":
      return { ...START, found: event.count, dropped: event.dropped ?? 0 };
    case "checking":
      if (state.slots.some((slot) => slot.productId === event.product_id)) return state;
      return {
        ...state,
        slots: [...state.slots, { productId: event.product_id, title: event.title, thumb: event.thumb ?? null, state: "checking" }],
      };
    case "verified": {
      const known = state.slots.some((slot) => slot.productId === event.product_id);
      const slots = known
        ? state.slots.map((slot) => (slot.productId === event.product_id ? { ...slot, state: event.status } : slot))
        : [...state.slots, { productId: event.product_id, title: "", thumb: null, state: event.status }];
      return { ...state, slots };
    }
    default:
      return state;
  }
}

export function ResultsScreen() {
  const isClient = useIsClient();
  const session = sessionStore.use();
  const results = resultsStore.use();
  const [retries, setRetries] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const [finishedHere, setFinishedHere] = useState(false);

  const current =
    session && results && results.sessionId === session.sessionId && results.searchRun === session.searchRun ? results : null;

  function finish(done: DoneEvent, ranWith: SearchSession) {
    resultsStore.set({ sessionId: ranWith.sessionId, searchRun: ranWith.searchRun, brief: ranWith.brief, done });
    setFinishedHere(true);
    setAnnouncement(text.summary(done.checked, done.passed));
  }

  return (
    <>
      <AppHeader back={{ href: "/understood", label: text.back }} />
      <main className="shell pb-16 pt-2">
        <p role="status" aria-live="polite" className="sr-only">
          {announcement}
        </p>
        {!isClient ? (
          <ScreenPlaceholder />
        ) : !session ? (
          <NoSession />
        ) : current ? (
          <ResultsView session={session} results={current} focusHeading={finishedHere} onAnnounce={setAnnouncement} />
        ) : (
          <SearchProgress
            key={`${session.sessionId}:${session.searchRun}:${retries}`}
            session={session}
            onDone={finish}
            onRetry={() => setRetries((count) => count + 1)}
          />
        )}
      </main>
    </>
  );
}

interface SearchProgressProps {
  session: SearchSession;
  onDone: (done: DoneEvent, ranWith: SearchSession) => void;
  onRetry: () => void;
}

function SearchProgress({ session, onDone, onRetry }: SearchProgressProps) {
  const router = useRouter();
  const [progress, dispatch] = useReducer(reduce, START);
  const finished = useRef(false);
  const handleDone = useEffectEvent((done: DoneEvent) => onDone(done, session));
  const { sessionId } = session;

  useEffect(() => {
    if (finished.current) return;
    const controller = new AbortController();
    let started = false;
    (async () => {
      try {
        for await (const event of searchProducts(sessionId, controller.signal)) {
          started = true;
          if (event.type === "done") {
            finished.current = true;
            handleDone(event);
            return;
          }
          dispatch({ type: "event", event });
        }
      } catch (error) {
        if (isAbort(error)) return;
        finished.current = true;
        const stoppedEarly = started && error instanceof ApiError && error.kind === "network";
        dispatch({ type: "failed", message: stoppedEarly ? text.stoppedEarly : errorMessage(error), retryable: canRetry(error) });
      }
    })();
    return () => controller.abort();
  }, [sessionId]);

  const { found, dropped, slots, error } = progress;
  const checked = slots.filter((slot) => slot.state !== "checking").length;
  const passed = slots.filter((slot) => slot.state === "pass").length;

  function startOver() {
    clearSearch();
    router.push("/");
  }

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="display text-[2.25rem] sm:text-[2.75rem]">{text.title}</h1>
        <p className="display mt-3 text-[1.5rem] font-semibold tabular-nums text-indigo" aria-hidden="true">
          {found === null ? text.starting : text.progress(found, slots.length, passed)}
        </p>
        {dropped > 0 ? <p className="mt-1 text-base text-ink-soft">{text.dropped(dropped)}</p> : null}
        <p role="status" aria-live="polite" className="sr-only">
          {found === null ? text.starting : checked === 0 ? text.liveFound(found) : text.liveProgress(checked, slots.length, passed)}
        </p>
      </div>

      {slots.length > 0 ? (
        <ul aria-label={text.slotsLabel} className="grid grid-cols-4 gap-2">
          {slots.map((slot) => (
            <li key={slot.productId} className="check-slot" data-state={slot.state}>
              {slot.thumb ? (
                <Image src={slot.thumb} alt="" fill unoptimized sizes="25vw" className="object-cover" />
              ) : (
                <span className="flex h-full items-center justify-center text-line-strong">
                  <PhotoIcon className="size-6" />
                </span>
              )}
              <span className="sr-only">{text.slotLabel(slot.title, text.slot[slot.state])}</span>
              {slot.state === "checking" ? null : (
                <span className="verdict" data-state={slot.state} aria-hidden="true">
                  <StatusIcon status={slot.state} className="size-[1.125rem]" />
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : error ? null : (
        <div aria-hidden="true" className="grid grid-cols-4 gap-2">
          {Array.from({ length: 8 }, (_, index) => (
            <span key={index} className="aspect-[3/4] rounded-[0.875rem] bg-wash" />
          ))}
        </div>
      )}

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

      {error ? (
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
