/** The link as an absolute http(s) URL, or null for anything else (javascript:, data:, junk). */
export function webLink(url: unknown, base?: string): string | null {
  if (typeof url !== "string" || url.trim() === "") return null;
  try {
    const parsed = new URL(url, base);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

/** Where a checkout link goes: a page in this app (our demo store), or a store's own site. */
export type CheckoutTarget = { kind: "app"; path: string } | { kind: "store"; url: string };

export function checkoutTarget(redirectUrl: string): CheckoutTarget | null {
  const href = webLink(redirectUrl, window.location.origin);
  if (!href) return null;
  const url = new URL(href);
  if (url.origin === window.location.origin) return { kind: "app", path: `${url.pathname}${url.search}` };
  return { kind: "store", url: url.href };
}
