"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, checkout, sendFeedback } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { checkoutTarget } from "@/lib/links";

export type BuyState =
  | { status: "idle" }
  | { status: "opening" }
  | { status: "opened"; url: string }
  | { status: "error"; message: string };

/**
 * POST /api/checkout, then go there: our demo store opens inside the app, a live store in a new tab.
 * The tab can only open once the request is back, so a browser may block it; "opened" keeps the
 * store's link so the shopper can tap it directly.
 */
export function useBuy(productId: string, sessionId: string | undefined) {
  const router = useRouter();
  const [state, setState] = useState<BuyState>({ status: "idle" });

  async function buy(size: string | null) {
    setState({ status: "opening" });
    try {
      const { redirect_url } = await checkout(productId, size);
      const target = checkoutTarget(redirect_url);
      if (!target) throw new ApiError("server");
      if (sessionId) void sendFeedback(sessionId, productId, "buy").catch(() => undefined);
      if (target.kind === "app") {
        router.push(target.path);
        return;
      }
      window.open(target.url, "_blank", "noopener,noreferrer");
      setState({ status: "opened", url: target.url });
    } catch (caught) {
      setState({ status: "error", message: errorMessage(caught) });
    }
  }

  return { state, buy };
}
