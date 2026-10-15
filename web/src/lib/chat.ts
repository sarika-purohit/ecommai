"use client";

import { ApiError, createBrief, isAbort, searchProducts, updateBrief } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { tradeoffLabel } from "@/lib/labels";
import {
  addWeb,
  pincodeStore,
  resultsStore,
  runKey,
  sameRule,
  sessionStore,
  sourceOf,
  sourceStore,
  stopWebWait,
  tradeoffChanges,
  updateSession,
  webWaitStore,
  clearSearch,
  type SearchSession,
} from "@/lib/session";
import { memoryValue, storedValue } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { BriefChange, DoneEvent, SearchSource, Tradeoff, WebEvent } from "@/lib/types";

/**
 * The AI chat. Its memory is the agent's checklist for one session: the first message makes it (POST /api/brief)
 * and every later message edits it (POST /api/brief/update), so "for men" after "cotton kurta under ₹1,200" searches
 * for men's cotton kurtas under ₹1,200. Both ask the agent never to hold the search up with a question.
 *
 * Searches run here, outside any component, so closing the chat or browsing doesn't stop them. Results go to the
 * same stores the results and product pages read.
 */

const text = strings.chat;
/** The agent sends "On other sites" within 25 s of the search starting; past this the stream is given up. */
const WEB_WAIT_MS = 40_000;
/** The AI chat looks in the demo catalogue until the shopper picks, so its results match the store's products. */
export const CHAT_DEFAULT_SOURCE: SearchSource = "catalog";

export interface ChatMessage {
  id: string;
  role: "user" | "ai";
  text: string;
  error?: boolean;
  /** "What to give up" choices, shown as buttons under an AI message when nothing matched. */
  tradeoffs?: { key: string; label: string; matches: number; changes: BriefChange[] }[];
}

export interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  /** False after "Back to all products": the store shows its own list until the next AI search. */
  showResults: boolean;
}

export type AiProgress =
  | { phase: "understanding" }
  | { phase: "checking"; found: number | null; total: number; checked: number; passed: number };

export const chatStore = storedValue<ChatState>("ecommai.chat", "session");
export const chatOpenStore = memoryValue<boolean>();
/** Set while a message is being read or a search is running. */
export const aiProgressStore = memoryValue<AiProgress>();

let controller: AbortController | null = null;

function chat(): ChatState {
  return chatStore.get() ?? { sessionId: null, messages: [], showResults: false };
}

function say(message: Omit<ChatMessage, "id">) {
  const current = chat();
  const id = `${Date.now().toString(36)}-${current.messages.length}`;
  chatStore.set({ ...current, messages: [...current.messages, { ...message, id }] });
}

export function chatSource(): SearchSource {
  return sourceStore.get() ?? CHAT_DEFAULT_SOURCE;
}

/** The chat's session, when the stored search is still the one it started. */
export function chatSession(): SearchSession | null {
  const session = sessionStore.get();
  return session && session.sessionId === chat().sessionId ? session : null;
}

export function isBusy(): boolean {
  return aiProgressStore.get() !== null;
}

/**
 * One message from the shopper. The first one (or one with a photo) starts a search; later ones change it.
 * Throws nothing: problems become an AI message.
 */
export async function sendMessage(words: string, photo: Blob | null = null): Promise<void> {
  const trimmed = words.trim();
  const pincode = pincodeStore.get();
  if (isBusy() || (!trimmed && !photo) || !pincode) return;
  say({ role: "user", text: trimmed || text.photoOnly });
  aiProgressStore.set({ phase: "understanding" });
  const session = chatSession();
  try {
    if (session && !photo) {
      const { brief } = await updateBrief(session.sessionId, { type: "text", text: trimmed }, false);
      updateSession((current) => ({ ...current, brief, searchRun: current.searchRun + 1 }));
    } else {
      if (session && photo) say({ role: "ai", text: text.photoNewSearch });
      const source = chatSource();
      const response = await createBrief({
        text: trimmed || undefined,
        image: photo ?? undefined,
        pincode,
        language: strings.language,
        source,
        ask: false,
      });
      sessionStore.set({
        sessionId: response.session_id,
        requestText: trimmed,
        hasPhoto: Boolean(photo),
        source,
        brief: { ...response.brief, question: null },
        dismissedQuestion: null,
        rulesOff: [],
        searchRun: 0,
      });
      resultsStore.set(null);
      chatStore.set({ ...chat(), sessionId: response.session_id });
    }
  } catch (error) {
    aiProgressStore.set(null);
    say({ role: "ai", text: errorMessage(error), error: true });
    return;
  }
  await runSearch();
}

/** Checklist edits from a chip or a "what to give up" button, then a fresh search. */
export async function applyChanges(changes: BriefChange[], note: string): Promise<void> {
  const session = chatSession();
  if (!session || isBusy() || changes.length === 0) return;
  aiProgressStore.set({ phase: "understanding" });
  try {
    let brief = session.brief;
    for (const change of changes) ({ brief } = await updateBrief(session.sessionId, change, false));
    const switchedOff = changes.flatMap((c) => (c.type === "set_rule" && !c.enabled ? [c.must_have] : []));
    const newSource = changes.flatMap((c) => (c.type === "set_source" ? [c.source] : [])).pop();
    updateSession((current) => ({
      ...current,
      brief,
      source: newSource ?? current.source,
      rulesOff: [...current.rulesOff.filter((off) => !switchedOff.some((m) => sameRule(m, off))), ...switchedOff],
      searchRun: current.searchRun + 1,
    }));
    if (newSource) sourceStore.set(newSource);
    say({ role: "ai", text: note });
  } catch (error) {
    aiProgressStore.set(null);
    say({ role: "ai", text: errorMessage(error), error: true });
    return;
  }
  await runSearch();
}

/** Where to look next. Mid-chat, the current checklist is searched again there. */
export async function setChatSource(source: SearchSource): Promise<void> {
  sourceStore.set(source);
  const session = chatSession();
  if (session && sourceOf(session) !== source) {
    await applyChanges([{ type: "set_source", source }], strings.source.searched[source]);
  }
}

export function newChat() {
  controller?.abort();
  controller = null;
  aiProgressStore.set(null);
  clearSearch();
  chatStore.set({ sessionId: null, messages: [], showResults: false });
}

export function showStore() {
  chatStore.set({ ...chat(), showResults: false });
}

async function runSearch(): Promise<void> {
  const session = sessionStore.get();
  if (!session) {
    aiProgressStore.set(null);
    return;
  }
  controller?.abort();
  const mine = new AbortController();
  controller = mine;
  chatStore.set({ ...chat(), showResults: true });
  const progress = { found: null as number | null, total: 0, checked: 0, passed: 0 };
  aiProgressStore.set({ phase: "checking", ...progress });
  let done = false;
  let giveUp: ReturnType<typeof setTimeout> | undefined;
  let web: WebEvent | null = null;
  try {
    for await (const event of searchProducts(session.sessionId, mine.signal)) {
      if (event.type === "web") {
        if (done) addWeb(session, event);
        else web = event;
        continue;
      }
      if (event.type === "done") {
        done = true;
        finish(session, event, web);
        giveUp = setTimeout(() => mine.abort(), WEB_WAIT_MS);
        continue;
      }
      if (event.type === "candidates") progress.found = event.count;
      if (event.type === "checking") progress.total += 1;
      if (event.type === "verified") {
        progress.checked += 1;
        if (event.status === "pass") progress.passed += 1;
      }
      if (controller === mine) aiProgressStore.set({ phase: "checking", ...progress });
    }
  } catch (error) {
    if (!done && !isAbort(error) && controller === mine) {
      aiProgressStore.set(null);
      const stoppedEarly = progress.total > 0 && error instanceof ApiError && error.kind === "network";
      say({ role: "ai", text: stoppedEarly ? strings.results.stoppedEarly : errorMessage(error), error: true });
    }
  } finally {
    clearTimeout(giveUp);
    if (done) stopWebWait(session);
    if (controller === mine) controller = null;
  }
}

function finish(session: SearchSession, done: DoneEvent, web: WebEvent | null) {
  const source = sourceOf(session);
  if (!web && source === "live") webWaitStore.set(runKey(session));
  resultsStore.set({ sessionId: session.sessionId, searchRun: session.searchRun, brief: session.brief, source, done, web });
  aiProgressStore.set(null);
  if (done.checked === 0) {
    say({ role: "ai", text: strings.results.nothingFound[source] });
    return;
  }
  const tradeoffs = done.passed === 0 ? done.tradeoffs.map((tradeoff) => tradeoffButton(tradeoff, session)) : [];
  const almost = done.almost.length > 0 ? text.almost(done.almost.length) : "";
  say({
    role: "ai",
    text: text.found(done.checked, done.passed) + almost + (tradeoffs.length > 0 ? ` ${text.tradeoffs}` : ""),
    tradeoffs: tradeoffs.length > 0 ? tradeoffs : undefined,
  });
}

function tradeoffButton(tradeoff: Tradeoff, session: SearchSession) {
  return {
    key: tradeoff.relax,
    label: tradeoffLabel(tradeoff, session.brief),
    matches: tradeoff.matches,
    changes: tradeoffChanges(tradeoff, session.brief),
  };
}
