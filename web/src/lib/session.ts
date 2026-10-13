"use client";

import { tradeoffAttribute } from "@/lib/labels";
import { storedValue } from "@/lib/stored";
import type { BriefChange, DoneEvent, FeedbackAction, MustHave, ResultCard, ShoppingBrief, Tradeoff } from "@/lib/types";

export interface SearchSession {
  sessionId: string;
  requestText: string;
  hasPhoto: boolean;
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
  done: DoneEvent;
}

export const pincodeStore = storedValue<string>("ecommai.pincode", "local");

export const sessionStore = storedValue<SearchSession>("ecommai.session", "session");

export const resultsStore = storedValue<StoredResults>("ecommai.results", "session");

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
