"use client";

import Image from "next/image";
import Link from "next/link";
import { useId, useState } from "react";
import { ArrowRightIcon, BookmarkIcon, CalendarIcon, HeartIcon } from "@/components/icons";
import { TickList } from "@/components/proof/TickList";
import { sendFeedback } from "@/lib/api";
import { rupees, shortDay } from "@/lib/format";
import { codNote, totalNote } from "@/lib/price";
import { feedbackStore } from "@/lib/session";
import { strings } from "@/lib/strings";
import type { FeedbackAction, ResultCard, ShoppingBrief } from "@/lib/types";

const text = strings.card;

interface ResultCardViewProps {
  card: ResultCard;
  brief: ShoppingBrief;
  sessionId: string;
}

export function ResultCardView({ card, brief, sessionId }: ResultCardViewProps) {
  const titleId = useId();
  const href = `/product/${encodeURIComponent(card.product_id)}`;
  const thumb = card.thumb ?? card.photo;
  const cod = codNote(card);

  return (
    <article aria-labelledby={titleId} className="panel p-4">
      <div className="flex gap-4">
        <Link
          href={href}
          tabIndex={-1}
          aria-hidden="true"
          className="relative block w-24 flex-none self-start overflow-hidden rounded-xl bg-card ring-1 ring-line"
        >
          {thumb ? (
            <Image src={thumb} alt="" width={300} height={400} unoptimized className="block h-auto w-full" />
          ) : (
            <span className="block aspect-[3/4] bg-wash" />
          )}
        </Link>
        <div className="min-w-0 flex-1">
          {card.brand ? <p className="eyebrow text-[0.8125rem] leading-tight">{card.brand}</p> : null}
          <h3 id={titleId} className="mt-0.5 line-clamp-2 font-semibold leading-snug">
            <Link href={href} className="hover:underline">
              {card.title}
            </Link>
          </h3>
          <p className="display mt-2 text-[1.75rem] tabular-nums">{rupees(card.total_price)}</p>
          <p className="text-base leading-snug text-ink-soft">{totalNote(card)}</p>
          {cod ? <p className="text-base leading-snug text-ink-soft">{cod}</p> : null}
          <p className="mt-1.5 flex items-center gap-1.5 text-base font-semibold">
            <CalendarIcon className="size-4 flex-none text-indigo" />
            {card.delivery_date ? text.arrives(shortDay(card.delivery_date)) : text.noDate}
          </p>
        </div>
      </div>

      {card.demo_data ? (
        <p className="mt-3">
          <span className="demo-badge">{text.demo(card.price?.demo ?? true)}</span>
        </p>
      ) : null}

      <div className="mt-4">
        <TickList ticks={card.ticks} brief={brief} photo={card.photo ?? card.thumb} />
      </div>
      {card.reason ? <p className="mt-3 text-base leading-snug text-ink-soft">{card.reason}</p> : null}

      <Feedback sessionId={sessionId} productId={card.product_id} title={card.title} href={href} />
    </article>
  );
}

function Feedback({ sessionId, productId, title, href }: { sessionId: string; productId: string; title: string; href: string }) {
  const sent = feedbackStore.use()?.[productId] ?? [];
  const [failed, setFailed] = useState(false);

  function remember(action: FeedbackAction, on: boolean) {
    const current = feedbackStore.get() ?? {};
    const actions = (current[productId] ?? []).filter((a) => a !== action);
    feedbackStore.set({ ...current, [productId]: on ? [...actions, action] : actions });
  }

  async function send(action: FeedbackAction) {
    if (sent.includes(action)) return;
    setFailed(false);
    remember(action, true);
    try {
      await sendFeedback(sessionId, productId, action);
    } catch {
      remember(action, false);
      setFailed(true);
    }
  }

  const buttons: { action: FeedbackAction; icon: typeof HeartIcon; idle: string; done: string }[] = [
    { action: "like", icon: HeartIcon, idle: text.like, done: text.liked },
    { action: "save", icon: BookmarkIcon, idle: text.save, done: text.saved },
  ];

  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="flex items-center gap-2">
        {buttons.map(({ action, icon: Icon, idle, done }) => {
          const pressed = sent.includes(action);
          return (
            <button
              key={action}
              type="button"
              className={`btn btn-small gap-1.5 px-3 ${pressed ? "bg-wash text-indigo" : "text-ink hover:bg-wash"}`}
              aria-pressed={pressed}
              aria-label={text.feedbackFor(pressed ? done : idle, title)}
              onClick={() => void send(action)}
            >
              <Icon className="size-5" fill={pressed ? "currentColor" : "none"} />
              {pressed ? done : idle}
            </button>
          );
        })}
        <Link href={href} className="link ml-auto no-underline hover:underline">
          {text.seeProduct}
          <ArrowRightIcon className="size-4" />
        </Link>
      </div>
      <p role="status" className="field-error">
        {failed ? text.feedbackFailed : null}
      </p>
    </div>
  );
}
