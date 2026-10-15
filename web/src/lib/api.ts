"use client";

import { getIdToken } from "@/lib/auth";
import { apiBaseUrl, mockApi } from "@/lib/config";
import { blobToBase64, shrinkPhoto } from "@/lib/image";
import { webLink } from "@/lib/links";
import { readSse } from "@/lib/sse";
import {
  SEARCH_EVENT_TYPES,
  type BriefChange,
  type BriefInput,
  type BriefRequestBody,
  type BriefResponse,
  type BriefUpdateRequest,
  type BriefUpdateResponse,
  type CatalogPage,
  type CatalogQuery,
  type CategoryCount,
  type CheckoutRequest,
  type CheckoutResponse,
  type DoneEvent,
  type FeedbackAction,
  type FeedbackRequest,
  type GenderFilter,
  type Me,
  type MeUpdate,
  type MustHave,
  type ProductDetails,
  type ResultCard,
  type RulesBody,
  type SearchEvent,
  type SearchRequest,
  type ShoppingBrief,
  type WebEvent,
  type WebResult,
} from "@/lib/types";

export type ApiErrorKind = "network" | "unauthorized" | "busy" | "badRequest" | "notFound" | "server";

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status: number | null = null,
    readonly retryable: boolean = kind === "network" || kind === "busy" || kind === "server",
    /** A message from the agent that is safe to show the shopper, e.g. from a stream `error` event. */
    readonly agentMessage: string | null = null,
  ) {
    super(agentMessage ?? kind);
    this.name = "ApiError";
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function kindFor(status: number): ApiErrorKind {
  if (status === 401 || status === 403) return "unauthorized";
  if (status === 404) return "notFound";
  if (status === 429 || status === 503) return "busy";
  if (status >= 400 && status < 500) return "badRequest";
  return "server";
}

async function headers(extra: Record<string, string>): Promise<Record<string, string>> {
  const token = await getIdToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

async function send(path: string, init: RequestInit & { headers?: Record<string, string> }): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers: await headers(init.headers ?? {}) });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new ApiError("network");
  }
  if (!response.ok) throw new ApiError(kindFor(response.status), response.status);
  return response;
}

async function requestJson<T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown): Promise<T> {
  const response = await send(path, {
    method,
    headers: body === undefined ? { Accept: "application/json" } : { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("server", response.status);
  }
}

// The mock and its sample catalogue load only in mock mode, so real builds never ship them.
type MockModule = typeof import("@/lib/mock");

function loadMock(): Promise<MockModule> {
  return import("@/lib/mock");
}

function isMockNotFound(error: unknown): boolean {
  return error instanceof Error && error.name === "MockNotFound";
}

async function viaMock<T>(call: (mock: MockModule) => Promise<T>): Promise<T> {
  try {
    return await call(await loadMock());
  } catch (error) {
    if (isMockNotFound(error)) throw new ApiError("notFound", 404, false);
    throw error;
  }
}

/** Fills in fields an older agent might leave out, so screens can rely on them. */
function withDefaults(brief: ShoppingBrief): ShoppingBrief {
  return { ...brief, not_checkable: brief.not_checkable ?? [], missing: brief.missing ?? [] };
}

async function briefBody(input: BriefInput): Promise<BriefRequestBody> {
  const body: BriefRequestBody = { pincode: input.pincode, language: input.language, source: input.source };
  if (input.ask !== undefined) body.ask = input.ask;
  if (input.text) body.text = input.text;
  if (typeof input.image === "string") {
    body.image_url = input.image;
  } else if (input.image) {
    const photo = await shrinkPhoto(input.image);
    body.image_base64 = await blobToBase64(photo);
    body.image_mime_type = photo.type || "image/jpeg";
  }
  return body;
}

/** POST /api/brief: turn the shopper's words and photo into a checklist. */
export async function createBrief(input: BriefInput): Promise<BriefResponse> {
  const response = mockApi
    ? await viaMock((mock) => mock.createBrief(input))
    : await requestJson<BriefResponse>("POST", "/api/brief", await briefBody(input));
  return { ...response, brief: withDefaults(response.brief) };
}

/**
 * POST /api/brief/update: apply one edit to the checklist and get the whole checklist back. The agent edits the
 * checklist it saved for this session, so a follow-up like "for men" builds on everything said before.
 */
export async function updateBrief(sessionId: string, change: BriefChange, ask?: boolean): Promise<BriefUpdateResponse> {
  const body: BriefUpdateRequest = { session_id: sessionId, change };
  if (ask !== undefined) body.ask = ask;
  const response = mockApi
    ? await viaMock((mock) => mock.updateBrief(sessionId, change))
    : await requestJson<BriefUpdateResponse>("POST", "/api/brief/update", body);
  return { brief: withDefaults(response.brief) };
}

function withCardDefaults(card: ResultCard): ResultCard {
  return {
    ...card,
    source: card.source ?? "catalog",
    store: card.store ?? null,
    product_url: card.product_url ?? null,
    shipping_note: card.shipping_note ?? null,
  };
}

function withDoneDefaults(event: DoneEvent): DoneEvent {
  return {
    ...event,
    rejected: event.rejected ?? {},
    duplicates: event.duplicates ?? 0,
    tradeoffs: event.tradeoffs ?? [],
    matches: (event.matches ?? []).map(withCardDefaults),
    almost: (event.almost ?? []).map(withCardDefaults),
    not_checkable: event.not_checkable ?? [],
  };
}

/**
 * Drops results without a title or an http(s) link, so a bad link can never run script when tapped. Fills in
 * the page-check fields an older agent leaves out; a photo must be an https link.
 */
function withWebDefaults(event: WebEvent): WebEvent {
  const results = (Array.isArray(event.results) ? event.results : []).flatMap((result): WebResult[] => {
    const url = webLink(result?.url);
    if (!url || !result.title) return [];
    const site = result.site || new URL(url).hostname.replace(/^www\./, "");
    const read = result.read === true;
    const ticks = read && Array.isArray(result.ticks) ? result.ticks : [];
    const image = webLink(result.image_url);
    return [
      {
        ...result,
        url,
        site,
        price_text: result.price_text || null,
        snippet: result.snippet || null,
        read,
        unread_reason: read ? null : (result.unread_reason ?? null),
        store: result.store || null,
        price_inr: typeof result.price_inr === "number" && result.price_inr > 0 ? result.price_inr : null,
        image_url: image?.startsWith("https:") ? image : null,
        sizes_in_stock: Array.isArray(result.sizes_in_stock) ? result.sizes_in_stock : [],
        ticks,
        overall: read ? (result.overall ?? null) : null,
      },
    ];
  });
  const html = event.search_entry_point_html;
  return {
    ...event,
    results,
    search_entry_point_html: typeof html === "string" && html.trim() ? html : null,
    queries: Array.isArray(event.queries) ? event.queries : [],
  };
}

function toSearchEvent(name: string, data: string): SearchEvent | null {
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return null;
  }
  const type = name !== "message" ? name : String(payload.type ?? "");
  if (!SEARCH_EVENT_TYPES.has(type as SearchEvent["type"])) return null;
  const event = { ...payload, type } as SearchEvent;
  if (event.type === "done") return withDoneDefaults(event);
  if (event.type === "web") return withWebDefaults(event);
  return event;
}

/**
 * POST /api/search: the live checking stream, read until the agent closes it. `done` brings the main results;
 * on live searches a `web` event follows it. Before `done`, an `error` event becomes an ApiError carrying the
 * agent's message and a stream that stops is a network error. After `done` the results are in, so a dropped
 * stream or a late error just ends it.
 */
export async function* searchProducts(sessionId: string, signal?: AbortSignal): AsyncGenerator<SearchEvent> {
  const source = mockApi ? mockStream(sessionId, signal) : liveStream(sessionId, signal);
  let done = false;
  try {
    for await (const event of source) {
      if (event.type === "error") {
        if (done) return;
        throw new ApiError(event.retryable ? "server" : "badRequest", null, event.retryable, event.message);
      }
      yield event;
      if (event.type === "done") done = true;
    }
  } catch (error) {
    if (isMockNotFound(error)) throw new ApiError("notFound", 404, false);
    if (done && !isAbort(error)) return;
    throw error;
  }
  if (!done) throw new ApiError("network");
}

async function* mockStream(sessionId: string, signal?: AbortSignal): AsyncGenerator<SearchEvent> {
  const mock = await loadMock();
  yield* mock.streamSearch(sessionId, signal);
}

async function* liveStream(sessionId: string, signal?: AbortSignal): AsyncGenerator<SearchEvent> {
  const body: SearchRequest = { session_id: sessionId };
  const response = await send("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.body) throw new ApiError("network");
  try {
    for await (const message of readSse(response.body)) {
      const event = toSearchEvent(message.event, message.data);
      if (event) yield event;
    }
  } catch (error) {
    if (isAbort(error) || error instanceof ApiError) throw error;
    throw new ApiError("network");
  }
}

/**
 * GET /api/products/{id}, with the tick-list for this session's checklist when a session is given.
 * Live ids are Shopify GIDs ("gid://shopify/p/…"), so the id is always URL-encoded.
 */
export async function getProduct(productId: string, sessionId?: string): Promise<ProductDetails> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const product = mockApi
    ? await viaMock((mock) => mock.getProduct(productId, sessionId))
    : await requestJson<ProductDetails>("GET", `/api/products/${encodeURIComponent(productId)}${query}`);
  return {
    ...product,
    source: product.source ?? "catalog",
    store: product.store ?? null,
    product_url: product.product_url ?? null,
    checkout_url: product.checkout_url ?? null,
  };
}

/** POST /api/feedback: likes, saves and purchases teach the agent the shopper's style. */
export async function sendFeedback(sessionId: string, productId: string, action: FeedbackAction): Promise<void> {
  const body: FeedbackRequest = { session_id: sessionId, product_id: productId, action };
  if (mockApi) {
    await viaMock((mock) => mock.sendFeedback(body));
    return;
  }
  await requestJson("POST", "/api/feedback", body);
}

/** POST /api/checkout: where to send the shopper to buy this product in this size. */
export async function checkout(productId: string, size: string | null): Promise<CheckoutResponse> {
  const body: CheckoutRequest = { product_id: productId, size };
  if (mockApi) return viaMock((mock) => mock.checkout(body));
  return requestJson<CheckoutResponse>("POST", "/api/checkout", body);
}

/** GET /api/rules: the shopper's saved personal rules. */
export async function getRules(): Promise<MustHave[]> {
  const response = mockApi ? await viaMock((mock) => mock.getRules()) : await requestJson<RulesBody>("GET", "/api/rules");
  return response.rules;
}

/** PUT /api/rules: replaces every saved rule. */
export async function saveRules(rules: MustHave[]): Promise<MustHave[]> {
  const body: RulesBody = { rules };
  const response = mockApi ? await viaMock((mock) => mock.saveRules(body)) : await requestJson<RulesBody>("PUT", "/api/rules", body);
  return response.rules ?? rules;
}

/** GET /api/me */
export async function getMe(): Promise<Me> {
  if (mockApi) return viaMock((mock) => mock.getMe());
  return requestJson<Me>("GET", "/api/me");
}

/** PUT /api/me: change the saved pincode or language. */
export async function updateMe(change: MeUpdate): Promise<void> {
  if (mockApi) {
    await viaMock((mock) => mock.updateMe(change));
    return;
  }
  await send("/api/me", {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(change),
  });
}

/** DELETE /api/me: removes the shopper's profile, rules, learned style and past searches. */
export async function deleteMe(): Promise<void> {
  if (mockApi) {
    await viaMock((mock) => mock.deleteMe());
    return;
  }
  await requestJson("DELETE", "/api/me");
}

/** DELETE /api/me/taste: forget the learned style. */
export async function clearTaste(): Promise<void> {
  if (mockApi) {
    await viaMock((mock) => mock.clearTaste());
    return;
  }
  await requestJson("DELETE", "/api/me/taste");
}

function catalogPath(query: CatalogQuery): string {
  const params = new URLSearchParams();
  if (query.q?.trim()) params.set("q", query.q.trim());
  if (query.category) params.set("category", query.category);
  if (query.gender) params.set("gender", query.gender);
  if (query.sort && query.sort !== "relevance") params.set("sort", query.sort);
  if (query.page && query.page > 1) params.set("page", String(query.page));
  if (query.page_size) params.set("page_size", String(query.page_size));
  const search = params.toString();
  return `/api/catalog${search ? `?${search}` : ""}`;
}

/** GET /api/catalog: the demo catalogue for the store pages, listed or searched by keyword. No AI involved. */
export async function getCatalog(query: CatalogQuery): Promise<CatalogPage> {
  if (mockApi) return viaMock((mock) => mock.getCatalog(query));
  return requestJson<CatalogPage>("GET", catalogPath(query));
}

/** GET /api/catalog/categories: product types and how many of each, for the category strip. */
export async function getCategories(gender: GenderFilter | null): Promise<CategoryCount[]> {
  if (mockApi) return viaMock((mock) => mock.getCategories(gender));
  return requestJson<CategoryCount[]>("GET", `/api/catalog/categories${gender ? `?gender=${gender}` : ""}`);
}
