"use client";

import { getIdToken } from "@/lib/auth";
import { apiBaseUrl, mockApi } from "@/lib/config";
import { blobToBase64, shrinkPhoto } from "@/lib/image";
import * as mock from "@/lib/mock";
import { readSse } from "@/lib/sse";
import {
  SEARCH_EVENT_TYPES,
  type BriefChange,
  type BriefInput,
  type BriefRequestBody,
  type BriefResponse,
  type BriefUpdateRequest,
  type BriefUpdateResponse,
  type ProductDetails,
  type SearchEvent,
  type SearchRequest,
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

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await send(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  return (await response.json()) as T;
}

function fromMock<T>(promise: Promise<T>): Promise<T> {
  return promise.catch((error: unknown) => {
    if (error instanceof mock.MockNotFound) throw new ApiError("notFound", 404, false);
    throw error;
  });
}

async function briefBody(input: BriefInput): Promise<BriefRequestBody> {
  const body: BriefRequestBody = { pincode: input.pincode, language: input.language };
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
  if (mockApi) return fromMock(mock.createBrief(input));
  return postJson<BriefResponse>("/api/brief", await briefBody(input));
}

/** POST /api/brief/update: apply one edit to the checklist and get the whole checklist back. */
export async function updateBrief(sessionId: string, change: BriefChange): Promise<BriefUpdateResponse> {
  if (mockApi) return fromMock(mock.updateBrief(sessionId, change));
  const body: BriefUpdateRequest = { session_id: sessionId, change };
  return postJson<BriefUpdateResponse>("/api/brief/update", body);
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
  return { ...payload, type } as SearchEvent;
}

/**
 * POST /api/search: the live checking stream. Ends after a `done` event; an `error` event
 * becomes an ApiError carrying the agent's message. A stream that stops early is a network error.
 */
export async function* searchProducts(sessionId: string, signal?: AbortSignal): AsyncGenerator<SearchEvent> {
  const source = mockApi ? mock.streamSearch(sessionId, signal) : liveStream(sessionId, signal);
  try {
    for await (const event of source) {
      if (event.type === "error") {
        throw new ApiError(event.retryable ? "server" : "badRequest", null, event.retryable, event.message);
      }
      yield event;
      if (event.type === "done") return;
    }
  } catch (error) {
    if (error instanceof mock.MockNotFound) throw new ApiError("notFound", 404, false);
    throw error;
  }
  throw new ApiError("network");
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

/** GET /api/products/{id}, with the tick-list for this session's checklist when a session is given. */
export async function getProduct(productId: string, sessionId?: string): Promise<ProductDetails> {
  if (mockApi) return fromMock(mock.getProduct(productId, sessionId));
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  const response = await send(`/api/products/${encodeURIComponent(productId)}${query}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return (await response.json()) as ProductDetails;
}
