"use client";

import { storedValue } from "@/lib/stored";
import type { MustHave, ShoppingBrief } from "@/lib/types";

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

export const pincodeStore = storedValue<string>("ecommai.pincode", "local");

export const sessionStore = storedValue<SearchSession>("ecommai.session", "session");

export function updateSession(change: (session: SearchSession) => SearchSession) {
  const current = sessionStore.get();
  if (current) sessionStore.set(change(current));
}

export function openQuestion(session: SearchSession): string | null {
  const question = session.brief.question;
  return question && question !== session.dismissedQuestion ? question : null;
}

export function sameRule(a: MustHave, b: MustHave): boolean {
  return a.attribute === b.attribute && a.rule === b.rule && String(a.value) === String(b.value);
}
