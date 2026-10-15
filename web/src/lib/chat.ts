"use client";

import { ApiError, createBrief, getCatalog, isAbort, searchProducts, updateBrief } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { mustHaveLabel, tradeoffLabel } from "@/lib/labels";
import { rupees } from "@/lib/format";
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
import { memoryValue } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { BriefChange, DoneEvent, SearchSource, ShoppingBrief, Tradeoff, WebEvent } from "@/lib/types";

/**
 * The AI chat. Its memory is the agent's checklist for one session: the first message makes it (POST /api/brief)
 * and every later message edits it (POST /api/brief/update), so "for men" after "cotton kurta under ₹1,200" searches
 * for men's cotton kurtas under ₹1,200. Both ask the agent never to hold the search up with a question; the one
 * thing the chat asks itself is the product type, when the shopper never said it, rather than search a guess.
 * Until they say it, their words are kept and read again together with the next message, so "something red" then
 * "tops for women" searches red tops for women, and a guessed product type never sticks. Meanwhile the shop shows
 * everything that fits what they did say ("red", "for women", "under ₹2,000"), every product type mixed together.
 *
 * The memory lasts while the page is open: "New search", a new search from the store's search bar or reloading
 * the page starts over, so an old checklist never leaks into a new request.
 *
 * Searches run here, outside any component, so closing the chat or browsing doesn't stop them. Results go to the
 * same stores the results and product pages read.
 */

const text = strings.chat;
/** The agent sends "On other sites" within 25 s of the search starting; past this the stream is given up. */
const WEB_WAIT_MS = 40_000;
/** The AI chat looks in the demo catalogue until the shopper picks, so its results match the store's products. */
export const CHAT_DEFAULT_SOURCE: SearchSource = "catalog";
/** Product types offered as one-tap answers when the shopper hasn't said what they want (the dataset's names). */
const PRODUCT_PICKS = [
  "Kurtas", "Tops", "Tshirts", "Shirts", "Jeans", "Trousers", "Watches", "Casual Shoes", "Sports Shoes", "Handbags",
  "Backpacks", "Sunglasses",
];

export interface ChatMessage {
  id: string;
  role: "user" | "ai";
  text: string;
  error?: boolean;
  /** "What to give up" choices, shown as buttons under an AI message when nothing matched. */
  tradeoffs?: { key: string; label: string; matches: number; changes: BriefChange[] }[];
  /** One-tap product types under the message, sent as the shopper's next message. */
  picks?: string[];
}

export interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  /** False after "Back to all products": the store shows its own list until the next AI search. */
  showResults: boolean;
  /** What the shopper has said while the product type is unknown, read again together with their next message. */
  pending: string | null;
}

export type AiProgress =
  | { phase: "understanding" }
  | { phase: "checking"; found: number | null; total: number; checked: number; passed: number };

/** In memory only, so reloading the page starts a fresh chat. */
export const chatStore = memoryValue<ChatState>();
export const chatOpenStore = memoryValue<boolean>();
/** Set while a message is being read or a search is running. */
export const aiProgressStore = memoryValue<AiProgress>();

let controller: AbortController | null = null;

function chat(): ChatState {
  return chatStore.get() ?? { sessionId: null, messages: [], showResults: false, pending: null };
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
 * Resolves to a shop address to show when the product type is still unknown: every product that fits what was
 * said, of every type. Throws nothing: problems become an AI message.
 */
export async function sendMessage(words: string, photo: Blob | null = null): Promise<string | null> {
  const trimmed = words.trim();
  const pincode = pincodeStore.get();
  if (isBusy() || (!trimmed && !photo) || !pincode) return null;
  say({ role: "user", text: trimmed || text.photoOnly });
  aiProgressStore.set({ phase: "understanding" });
  const session = chatSession();
  // While the product type is unknown the checklist's type is only a guess, so it's never edited: the words are
  // read again from the start with the new ones.
  const pending = session ? chat().pending : null;
  let said = trimmed;
  let brief: ShoppingBrief;
  try {
    if (session && !photo && !pending) {
      ({ brief } = await updateBrief(session.sessionId, { type: "text", text: trimmed }, false));
      updateSession((current) => ({ ...current, brief, searchRun: current.searchRun + 1 }));
      said = `${session.requestText}. ${trimmed}`;
    } else {
      if (session && photo && !pending) say({ role: "ai", text: text.photoNewSearch });
      said = [pending, trimmed].filter(Boolean).join(". ");
      const source = chatSource();
      const response = await createBrief({
        text: said || undefined,
        image: photo ?? undefined,
        pincode,
        language: strings.language,
        source,
        ask: false,
      });
      sessionStore.set({
        sessionId: response.session_id,
        requestText: said,
        hasPhoto: Boolean(photo),
        source,
        brief: { ...response.brief, question: null },
        dismissedQuestion: null,
        rulesOff: [],
        searchRun: 0,
      });
      resultsStore.set(null);
      chatStore.set({ ...chat(), sessionId: response.session_id, showResults: false });
      brief = response.brief;
    }
  } catch (error) {
    aiProgressStore.set(null);
    say({ role: "ai", text: errorMessage(error), error: true });
    return null;
  }
  if (needsProduct(brief)) {
    // The agent had to guess the product type, so its checklist isn't searched. Show the shop's products that fit
    // what was said instead, and keep the words for the next message.
    chatStore.set({ ...chat(), pending: said || pending, showResults: false });
    const shown = await shopSearch(brief);
    aiProgressStore.set(null);
    const picks = PRODUCT_PICKS.map(productWord);
    if (shown) {
      const note = chatSource() === "live" ? text.liveNeedsType : "";
      say({ role: "ai", text: text.browsing(shown.what, shown.total) + note, picks });
      return shown.href;
    }
    say({ role: "ai", text: text.whatProduct(kept(brief)), picks });
    return null;
  }
  chatStore.set({ ...chat(), pending: null });
  await runSearch();
  return null;
}

function productWord(category: string): string {
  return strings.labels.categories[category] ?? category;
}

/**
 * The shop's keyword search for what the shopper said without a product type: colours, gender and budget, which
 * every product type has ("red for women under 2000"). Null when they said none of these or nothing matches.
 */
async function shopSearch(brief: ShoppingBrief): Promise<{ href: string; what: string; total: number } | null> {
  const colours = brief.must_haves.flatMap((m) =>
    m.attribute === "colour" && m.rule === "is" && typeof m.value === "string" ? [m.value.toLowerCase()] : [],
  );
  const gender = brief.gender === "men" || brief.gender === "women" ? brief.gender : null;
  const budget = brief.budget_max_inr;
  const rating = brief.min_rating;
  if (colours.length === 0 && !gender && budget === null && rating === null) return null;
  const q = [
    ...colours,
    gender ? `for ${gender}` : "",
    budget !== null ? `under ${budget}` : "",
    rating !== null ? `rated ${rating}+` : "",
  ].filter(Boolean).join(" ");
  try {
    const page = await getCatalog({ q, page_size: 1 });
    if (page.total === 0) return null;
    const what = [
      colours.join(" or ") || null,
      gender ? strings.store.understoodGender(strings.labels.genders[gender]) : null,
      budget !== null ? strings.store.understoodPrice(rupees(budget)) : null,
      rating !== null ? strings.store.understoodRating(rating) : null,
    ].filter(Boolean).join(" ");
    return { href: `/?${new URLSearchParams({ q })}`, what, total: page.total };
  } catch {
    return null;
  }
}

/** True when the shopper hasn't said what kind of product they want, so the checklist's product type is a guess. */
export function needsProduct(brief: ShoppingBrief): boolean {
  return brief.missing.includes("product");
}

function kept(brief: ShoppingBrief): string[] {
  const words = brief.must_haves.filter((m) => !m.from_rule).map(mustHaveLabel);
  if (brief.gender) words.push(strings.store.understoodGender(strings.labels.genders[brief.gender] ?? brief.gender));
  if (brief.budget_max_inr !== null) words.push(strings.store.understoodPrice(rupees(brief.budget_max_inr)));
  if (brief.min_rating !== null) words.push(strings.store.understoodRating(brief.min_rating));
  return words;
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
    if (needsProduct(brief)) {
      aiProgressStore.set(null);
      return;
    }
  } catch (error) {
    aiProgressStore.set(null);
    say({ role: "ai", text: errorMessage(error), error: true });
    return;
  }
  await runSearch();
}

/**
 * Where the AI looks next. With a product type known, the current checklist is searched again there; before that
 * the chat says so, since the shop's own list is always the demo catalogue.
 */
export async function setChatSource(source: SearchSource): Promise<void> {
  if (isBusy()) return;
  sourceStore.set(source);
  const session = chatSession();
  if (session && !needsProduct(session.brief)) {
    if (sourceOf(session) !== source) await applyChanges([{ type: "set_source", source }], strings.source.searched[source]);
    return;
  }
  if (session) updateSession((current) => ({ ...current, source }));
  if (chat().messages.length > 0) say({ role: "ai", text: text.sourceLater[source] });
}

export function newChat() {
  controller?.abort();
  controller = null;
  aiProgressStore.set(null);
  clearSearch();
  chatStore.set({ sessionId: null, messages: [], showResults: false, pending: null });
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
  // What this search couldn't check, e.g. a brand, or a star rating in live stores.
  const notes = done.not_checkable.length > 0 ? text.notChecked(done.not_checkable) : "";
  say({
    role: "ai",
    text: text.found(done.checked, done.passed) + almost + notes + (tradeoffs.length > 0 ? ` ${text.tradeoffs}` : ""),
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
