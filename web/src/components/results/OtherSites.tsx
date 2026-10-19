"use client";

import Image from "next/image";
import { useId, useState } from "react";
import { ExternalIcon, InfoIcon } from "@/components/icons";
import { TickList } from "@/components/proof/TickList";
import { rupees } from "@/lib/format";
import { strings } from "@/lib/strings";
import type { ShoppingBrief, WebEvent, WebResult } from "@/lib/types";

const text = strings.web;

/**
 * Google requires its Search Suggestions to look exactly as sent and to open Google's results page
 * when tapped. The frame keeps our styles out of them and theirs out of the page, runs no scripts, and
 * <base target="_blank"> sends each tap to Google in a new, unsandboxed tab. The body margin is reset so
 * the suggestions are as wide as the list. Google's chip row is one 48px line that scrolls sideways.
 */
function suggestionsDocument(html: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>html,body{margin:0}</style></head><body>${html}</body></html>`;
}

interface OtherSitesProps {
  web: WebEvent | null;
  /** The stream is still open after `done`: Google is reading the other sites' pages. */
  checking: boolean;
  brief: ShoppingBrief;
}

/** Products Grounding with Google Search found elsewhere. Kept apart from our checked results, never mixed in. */
export function OtherSites({ web, checking, brief }: OtherSitesProps) {
  const titleId = useId();
  const noteId = useId();
  if (!web) {
    if (!checking) return null;
    return (
      <section aria-labelledby={titleId} aria-busy="true">
        <hr className="stitch" />
        <div className="mt-6 rounded-[1.25rem] bg-unclear-wash p-4 ring-1 ring-line">
          <h2 id={titleId} className="display text-[1.5rem]">
            {text.title}
          </h2>
          <p role="status" className="mt-1 flex items-center gap-2 text-base font-semibold text-ink-soft">
            <span className="size-3 flex-none animate-pulse rounded-full bg-indigo" aria-hidden="true" />
            {text.checking}
          </p>
        </div>
      </section>
    );
  }
  if (web.results.length === 0 && !web.search_entry_point_html) return null;
  const anyRead = web.results.some((result) => result.read);

  return (
    <section aria-labelledby={titleId} aria-describedby={noteId}>
      <hr className="stitch" />
      <div className="mt-6 rounded-[1.25rem] bg-unclear-wash p-4 ring-1 ring-line">
        <h2 id={titleId} className="display text-[1.5rem]">
          {text.title}
        </h2>
        <p id={noteId} className="mt-1 flex gap-2 text-base font-semibold leading-snug text-ink-soft">
          <InfoIcon className="mt-0.5 size-5 flex-none" />
          {anyRead ? text.readNote : text.note}
        </p>
        {web.search_entry_point_html ? (
          <iframe
            title={text.suggestions}
            srcDoc={suggestionsDocument(web.search_entry_point_html)}
            sandbox="allow-popups allow-popups-to-escape-sandbox"
            className="mt-3 block h-12 w-full border-0"
          />
        ) : null}
        {web.results.length > 0 ? (
          <ul className="mt-2 divide-y divide-line">
            {web.results.map((result, index) => (
              <li key={`${index}:${result.url}`}>
                {result.read ? <CheckedPage result={result} brief={brief} /> : <PlainLink result={result} />}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}

function priceOf(result: WebResult): string | null {
  return result.price_inr !== null ? rupees(result.price_inr) : result.price_text;
}

/** A page Gemini read through Google, with its tick-list. The photo, like the button, opens the page itself. */
function CheckedPage({ result, brief }: { result: WebResult; brief: ShoppingBrief }) {
  const titleId = useId();
  const [photoFailed, setPhotoFailed] = useState(false);
  const photo = photoFailed ? null : result.image_url;

  return (
    <article aria-labelledby={titleId} className="py-4">
      <div className="flex gap-3">
        {photo ? (
          <a
            href={result.url}
            target="_blank"
            rel="noopener noreferrer"
            tabIndex={-1}
            aria-hidden="true"
            className="block w-20 flex-none self-start overflow-hidden rounded-xl bg-card ring-1 ring-line"
          >
            <Image
              src={photo}
              alt=""
              width={300}
              height={400}
              unoptimized
              referrerPolicy="no-referrer"
              className="block h-auto w-full"
              onError={() => setPhotoFailed(true)}
            />
          </a>
        ) : null}
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="line-clamp-2 font-semibold leading-snug">
            {result.title}
          </h3>
          <p className="mt-0.5 text-base text-ink-soft">{text.detail(result.store ?? result.site, priceOf(result))}</p>
          {result.sizes_in_stock.length > 0 ? (
            <p className="text-base leading-snug text-ink-soft">{text.sizes(result.sizes_in_stock)}</p>
          ) : null}
        </div>
      </div>
      <div className="mt-3">
        <TickList ticks={result.ticks} brief={brief} fromWeb />
      </div>
      <a href={result.url} target="_blank" rel="noopener noreferrer" className="btn btn-secondary btn-small mt-3 w-full">
        {text.openOn(result.site)}
        <ExternalIcon className="size-5 flex-none" />
        <span className="sr-only">{strings.common.newTab}</span>
      </a>
    </article>
  );
}

function PlainLink({ result }: { result: WebResult }) {
  return (
    <a href={result.url} target="_blank" rel="noopener noreferrer" className="flex min-h-14 items-start gap-3 py-3">
      <span className="min-w-0 flex-1">
        <span className="line-clamp-2 font-semibold leading-snug text-indigo underline decoration-2 underline-offset-4">
          {result.title}
        </span>
        <span className="mt-0.5 block text-base text-ink-soft">{text.detail(result.site, priceOf(result))}</span>
        <span className="mt-0.5 block text-base text-ink-soft">{text.notRead[result.unread_reason ?? "none"]}</span>
      </span>
      <ExternalIcon className="mt-1 size-5 flex-none text-indigo" />
      <span className="sr-only">{strings.common.newTab}</span>
    </a>
  );
}
