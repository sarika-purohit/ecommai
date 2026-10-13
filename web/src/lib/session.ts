"use client";

import { tradeoffAttribute } from "@/lib/labels";
import { memoryValue, storedValue } from "@/lib/stored";
import type {
  BriefChange,
  DoneEvent,
  FeedbackAction,
  MustHave,
  ResultCard,
  SearchSource,
  ShoppingBrief,
  Tradeoff,
  WebEvent,
} from "@/lib/types";

/** Where a new search looks until the shopper picks; the agent's default too. */
export const DEFAULT_SOURCE: SearchSource = "live";

export interface SearchSession {
  sessionId: string;
  requestText: string;
  hasPhoto: boolean;
  /** Missing on searches started before the choice existed, which searched the catalogue. */
  source?: SearchSource;
  brief: ShoppingBrief;
  /** The agent's question the shopper chose to skip, so it stays hidden after later edits. */
  dismissedQuestion: string | null;
  /** Personal rules switched off for this search. The agent drops them from the brief, so we keep them to show. */
  rulesOff: MustHave[];
  /** Changes each time "Find matches" is tapped, so the checking screen starts fresh. */
  searchRun: number;
}

/** The finished search, so going back from a product page shows the results without checking again. */
export interface StoredResults {
  sessionId: string;
  searchRun: number;
  /** The checklist this search ran with, for tick labels. */
  brief: ShoppingBrief;
  source?: SearchSource;
  done: DoneEvent;
  /** "On other sites", when grounding found anything. Arrives after `done`. */
  web?: WebEvent | null;
}

export const pincodeStore = storedValue<string>("ecommai.pincode", "local");

/** The shopper's last choice of where to look, used for the next search. */
export const sourceStore = storedValue<SearchSource>("ecommai.source", "local");

export function sourceOf(search: { source?: SearchSource } | null): SearchSource {
  return search?.source ?? "catalog";
}

export const sessionStore = storedValue<SearchSession>("ecommai.session", "session");

export const resultsStore = storedValue<StoredResults>("ecommai.results", "session");

/** Which search run's stream is still open after `done`, waiting for "On other sites". Gone on reload. */
export const webWaitStore = memoryValue<string>();

export function runKey(run: { sessionId: string; searchRun: number }): string {
  return `${run.sessionId}:${run.searchRun}`;
}

/** Adds a `web` event that arrived after `done` to the stored results, if they're still for that search run. */
export function addWeb(run: { sessionId: string; searchRun: number }, web: WebEvent) {
  const stored = resultsStore.get();
  if (stored && runKey(stored) === runKey(run)) resultsStore.set({ ...stored, web });
}

/** Stops waiting for "On other sites" for this run, unless a newer run is already waiting. */
export function stopWebWait(run: { sessionId: string; searchRun: number }) {
  if (webWaitStore.get() === runKey(run)) webWaitStore.set(null);
}

/** Likes and saves sent this session, by product id, so the buttons stay pressed. */
export const feedbackStore = storedValue<Record<string, FeedbackAction[]>>("ecommai.feedback", "session");

export function updateSession(change: (session: SearchSession) => SearchSession) {
  const current = sessionStore.get();
  if (current) sessionStore.set(change(current));
}

/** Forgets the current search, its results and feedback, e.g. on "New search" or sign-out. */
export function clearSearch() {
  sessionStore.set(null);
  resultsStore.set(null);
  feedbackStore.set(null);
  webWaitStore.set(null);
}

export function openQuestion(session: SearchSession): string | null {
  const question = session.brief.question;
  return question && question !== session.dismissedQuestion ? question : null;
}

export function sameRule(a: MustHave, b: MustHave): boolean {
  return a.attribute === b.attribute && a.rule === b.rule && String(a.value) === String(b.value);
}

/** The result card for a product from the last search, if it was shown. */
export function findCard(productId: string): ResultCard | null {
  const results = resultsStore.get();
  if (!results) return null;
  return [...results.done.matches, ...results.done.almost].find((card) => card.product_id === productId) ?? null;
}

/** The checklist edits one tap on a "what to give up" card makes. Rule must-haves are switched off, not removed. */
export function tradeoffChanges(tradeoff: Tradeoff, brief: ShoppingBrief): BriefChange[] {
  if (tradeoff.relax === "deadline") {
    return [{ type: "set_deadline", deadline_date: typeof tradeoff.to === "string" ? tradeoff.to : null }];
  }
  if (tradeoff.relax === "budget") {
    return [{ type: "set_budget", budget_max_inr: tradeoff.to === null ? null : Number(tradeoff.to) }];
  }
  const attribute = tradeoffAttribute(tradeoff);
  return brief.must_haves
    .filter((m) => m.attribute === attribute)
    .map((m): BriefChange => (m.from_rule ? { type: "set_rule", must_have: m, enabled: false } : { type: "remove_must_have", id: m.id }));
}
