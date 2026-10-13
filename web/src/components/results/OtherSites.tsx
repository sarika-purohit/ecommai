"use client";

import { useId } from "react";
import { ExternalIcon, InfoIcon } from "@/components/icons";
import { strings } from "@/lib/strings";
import type { WebEvent } from "@/lib/types";

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

/** Products Grounding with Google Search found elsewhere. Kept apart from our checked results, never mixed in. */
export function OtherSites({ web }: { web: WebEvent }) {
  const titleId = useId();
  const noteId = useId();
  if (web.results.length === 0 && !web.search_entry_point_html) return null;

  return (
    <section aria-labelledby={titleId} aria-describedby={noteId}>
      <hr className="stitch" />
      <div className="mt-6 rounded-[1.25rem] bg-unclear-wash p-4 ring-1 ring-line">
        <h2 id={titleId} className="display text-[1.5rem]">
          {text.title}
        </h2>
        <p id={noteId} className="mt-1 flex gap-2 text-base font-semibold leading-snug text-ink-soft">
          <InfoIcon className="mt-0.5 size-5 flex-none" />
          {text.note}
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
                <a href={result.url} target="_blank" rel="noopener noreferrer" className="flex min-h-14 items-start gap-3 py-3">
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2 font-semibold leading-snug text-indigo underline decoration-2 underline-offset-4">
                      {result.title}
                    </span>
                    <span className="mt-0.5 block text-base text-ink-soft">{text.detail(result.site, result.price_text)}</span>
                  </span>
                  <ExternalIcon className="mt-1 size-5 flex-none text-indigo" />
                  <span className="sr-only">{strings.common.newTab}</span>
                </a>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
