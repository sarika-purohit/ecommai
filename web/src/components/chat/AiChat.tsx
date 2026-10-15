"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from "react";
import { EditableTag } from "@/components/EditableTag";
import { PincodeCard } from "@/components/home/PincodeCard";
import { CameraIcon, CloseIcon, SendIcon, SparkleIcon } from "@/components/icons";
import { SourceChoice } from "@/components/SourceChoice";
import { Tag } from "@/components/Tag";
import {
  aiProgressStore,
  applyChanges,
  chatOpenStore,
  chatSource,
  chatStore,
  newChat,
  sendMessage,
  setChatSource,
  type AiProgress,
  type ChatMessage,
} from "@/lib/chat";
import { rupees, shortDay } from "@/lib/format";
import { categoryLabel, mustHaveLabel, niceToHaveLabel } from "@/lib/labels";
import { pincodeStore, resultsStore, sessionStore, sourceStore } from "@/lib/session";
import { useIsClient } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { BriefChange, ShoppingBrief } from "@/lib/types";

const text = strings.chat;

/** The round AI button and, once tapped, the chat. Lives on the store pages. */
export function AiChat() {
  const isClient = useIsClient();
  const open = chatOpenStore.use() ?? false;
  const progress = aiProgressStore.use();
  if (!isClient) return null;
  return open ? (
    <ChatPanel />
  ) : (
    <button type="button" className="ai-fab" aria-label={text.openLabel} onClick={() => chatOpenStore.set(true)}>
      <SparkleIcon className={`size-7 ${progress ? "animate-pulse" : ""}`} />
      <span className="hidden sm:inline">{text.open}</span>
    </button>
  );
}

interface Photo {
  file: File;
  url: string;
}

function ChatPanel() {
  const chat = chatStore.use();
  const session = sessionStore.use();
  const results = resultsStore.use();
  const progress = aiProgressStore.use();
  const pincode = pincodeStore.use();
  const source = sourceStore.use() ?? chatSource();
  const [draft, setDraft] = useState("");
  const [photo, setPhoto] = useState<Photo | null>(null);
  const titleId = useId();
  const inputId = useId();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pincodeRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const messages = chat?.messages ?? [];
  const mine = session && chat?.sessionId === session.sessionId ? session : null;
  const busy = progress !== null;
  const hasResults = Boolean(mine && results && results.sessionId === mine.sessionId);

  useEffect(() => {
    inputRef.current?.focus();
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") chatOpenStore.set(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, progress?.phase]);

  useEffect(() => {
    return () => {
      if (photo) URL.revokeObjectURL(photo.url);
    };
  }, [photo]);

  function send(words: string) {
    if (busy || !pincode || (!words.trim() && !photo)) return;
    const file = photo?.file ?? null;
    setDraft("");
    setPhoto(null);
    void sendMessage(words, file);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    send(draft);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send(draft);
    }
  }

  function choosePhoto(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    event.target.value = "";
    if (file && file.type.startsWith("image/")) setPhoto({ file, url: URL.createObjectURL(file) });
  }

  return (
    <>
      <div className="fixed inset-0 z-30 bg-ink/30 lg:hidden" aria-hidden="true" onClick={() => chatOpenStore.set(false)} />
      <section role="dialog" aria-labelledby={titleId} className="chat-panel">
        <header className="flex items-center gap-2 border-b border-line px-4 py-3">
          <SparkleIcon className="size-6 flex-none text-indigo" />
          <h2 id={titleId} className="display min-w-0 flex-1 truncate text-[1.375rem]">
            {text.title}
          </h2>
          {messages.length > 0 ? (
            <button type="button" className="btn btn-secondary btn-small min-h-10 px-3 text-base" onClick={newChat}>
              {text.newChat}
            </button>
          ) : null}
          <button
            type="button"
            className="flex size-11 flex-none items-center justify-center rounded-full hover:bg-wash"
            aria-label={text.close}
            onClick={() => chatOpenStore.set(false)}
          >
            <CloseIcon className="size-6" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          <div className="flex flex-col gap-3">
            <SourceChoice value={source} onChange={(next) => void setChatSource(next)} disabled={busy} />
            {!pincode ? (
              <div>
                <p className="mb-2 font-semibold">{text.needPincode}</p>
                <PincodeCard pincode={null} inputRef={pincodeRef} required={false} />
              </div>
            ) : null}

            {messages.length === 0 ? (
              <Intro disabled={busy || !pincode} onPick={send} />
            ) : (
              <ol className="flex flex-col gap-2.5" aria-live="polite">
                {messages.map((message) => (
                  <Message key={message.id} message={message} disabled={busy} />
                ))}
              </ol>
            )}
            {progress ? <Progress progress={progress} /> : null}
            {hasResults && !busy ? (
              <Link href="/results" className="link self-start" onClick={() => chatOpenStore.set(false)}>
                {text.seeDetails}
              </Link>
            ) : null}
            <div ref={endRef} />
          </div>
        </div>

        <div className="border-t border-line bg-card px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3">
          {mine ? <Memory brief={mine.brief} disabled={busy} /> : null}
          {photo ? (
            <div className="mb-2 flex items-center gap-2 rounded-xl bg-wash p-1.5">
              <span className="relative size-11 flex-none overflow-hidden rounded-lg bg-card">
                <Image src={photo.url} alt="" fill unoptimized sizes="44px" className="object-cover" />
              </span>
              <span className="min-w-0 flex-1 text-base font-semibold">{text.photoAdded}</span>
              <button
                type="button"
                className="flex size-10 items-center justify-center rounded-full hover:bg-card"
                aria-label={text.removePhoto}
                onClick={() => setPhoto(null)}
              >
                <CloseIcon className="size-5" />
              </button>
            </div>
          ) : null}
          <form onSubmit={submit} className="flex items-end gap-2">
            <button
              type="button"
              className="flex size-12 flex-none items-center justify-center rounded-full bg-wash text-indigo hover:bg-line"
              aria-label={text.addPhoto}
              onClick={() => fileRef.current?.click()}
              disabled={busy}
            >
              <CameraIcon className="size-6" />
            </button>
            <input ref={fileRef} type="file" accept="image/*" className="sr-only" tabIndex={-1} aria-hidden="true" onChange={choosePhoto} />
            <label htmlFor={inputId} className="sr-only">
              {text.inputLabel}
            </label>
            <textarea
              ref={inputRef}
              id={inputId}
              rows={1}
              className="field max-h-32 min-h-12 flex-1 resize-none py-2.5 text-[1.0625rem] leading-snug"
              placeholder={mine ? text.followPlaceholder : text.placeholder}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
            />
            <button
              type="submit"
              className="flex size-12 flex-none items-center justify-center rounded-full bg-indigo text-white hover:bg-indigo-deep disabled:opacity-50"
              aria-label={text.send}
              disabled={busy || !pincode || (!draft.trim() && !photo)}
            >
              <SendIcon className="size-6" />
            </button>
          </form>
          {busy ? <p className="mt-1.5 text-[0.9375rem] text-ink-soft">{text.busy}</p> : null}
        </div>
      </section>
    </>
  );
}

function Intro({ disabled, onPick }: { disabled: boolean; onPick: (words: string) => void }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="bubble" data-role="ai">
        {text.intro}
      </p>
      <p className="eyebrow mt-1">{text.examplesLabel}</p>
      <ul className="flex flex-col gap-2">
        {text.examples.map((example) => (
          <li key={example}>
            <button
              type="button"
              className="w-full rounded-2xl border-2 border-line bg-card px-3.5 py-2.5 text-left font-semibold transition hover:border-line-strong disabled:opacity-60"
              disabled={disabled}
              onClick={() => onPick(example)}
            >
              {example}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Message({ message, disabled }: { message: ChatMessage; disabled: boolean }) {
  return (
    <li className="flex flex-col gap-2">
      <p className="bubble" data-role={message.role} data-error={message.error ? "" : undefined}>
        <span className="sr-only">{message.role === "user" ? text.you : text.ai}: </span>
        {message.text}
      </p>
      {message.tradeoffs ? (
        <ul className="flex flex-col gap-1.5 pl-1">
          {message.tradeoffs.map((tradeoff) => (
            <li key={tradeoff.key}>
              <button
                type="button"
                className="flex w-full items-center justify-between gap-3 rounded-xl bg-marigold-wash px-3.5 py-2 text-left font-semibold ring-1 ring-marigold transition hover:ring-2 disabled:opacity-60"
                disabled={disabled}
                onClick={() => void applyChanges(tradeoff.changes, text.relaxed(tradeoff.label))}
              >
                <span>{tradeoff.label}</span>
                <span className="text-base font-normal text-ink-soft tabular-nums">
                  {strings.results.tradeoffMatches(tradeoff.matches)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function Progress({ progress }: { progress: AiProgress }) {
  const line =
    progress.phase === "understanding" ? text.understanding : text.checking(progress.checked, progress.total, progress.passed);
  return (
    <p className="bubble flex items-center gap-2" data-role="ai" role="status">
      <span className="size-2.5 flex-none animate-pulse rounded-full bg-indigo" aria-hidden="true" />
      {line}
    </p>
  );
}

/** What the AI is looking for now: the agent's checklist. Tapping ✕ drops a wish and searches again. */
function Memory({ brief, disabled }: { brief: ShoppingBrief; disabled: boolean }) {
  const remove = (change: BriefChange, label: string) => void applyChanges([change], text.removed(label));
  return (
    <div className="mb-2.5">
      <p className="eyebrow text-[0.8125rem]">{text.remembering}</p>
      <ul className="mt-1 flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
        <li>
          <Tag>
            <span className="tag-text">{categoryLabel(brief)}</span>
          </Tag>
        </li>
        {brief.must_haves.map((mustHave) => {
          const label = mustHaveLabel(mustHave);
          const change: BriefChange = mustHave.from_rule
            ? { type: "set_rule", must_have: mustHave, enabled: false }
            : { type: "remove_must_have", id: mustHave.id };
          return (
            <li key={mustHave.id}>
              <EditableTag
                label={label}
                editKey={`chat-${mustHave.id}`}
                tone={mustHave.from_rule ? "rule" : "plain"}
                disabled={disabled}
                onRemove={() => remove(change, label)}
              />
            </li>
          );
        })}
        {brief.nice_to_haves.map((nice) => {
          const label = niceToHaveLabel(nice);
          return (
            <li key={`${nice.attribute}:${String(nice.value)}`}>
              <EditableTag
                label={label}
                editKey={`chat-nice-${nice.attribute}`}
                disabled={disabled}
                onRemove={() => remove({ type: "remove_nice_to_have", attribute: nice.attribute, value: nice.value }, label)}
              />
            </li>
          );
        })}
        {brief.budget_max_inr !== null ? (
          <li>
            <EditableTag
              label={strings.labels.budget(rupees(brief.budget_max_inr))}
              editKey="chat-budget"
              disabled={disabled}
              onRemove={() => remove({ type: "set_budget", budget_max_inr: null }, strings.labels.budget(rupees(brief.budget_max_inr ?? 0)))}
            />
          </li>
        ) : null}
        {brief.deadline_date ? (
          <li>
            <EditableTag
              label={strings.labels.deadline(shortDay(brief.deadline_date))}
              editKey="chat-deadline"
              disabled={disabled}
              onRemove={() => remove({ type: "set_deadline", deadline_date: null }, strings.labels.deadline(shortDay(brief.deadline_date ?? "")))}
            />
          </li>
        ) : null}
        {brief.size ? (
          <li>
            <EditableTag
              label={strings.labels.size(brief.size)}
              editKey="chat-size"
              disabled={disabled}
              onRemove={() => remove({ type: "set_size", size: null }, strings.labels.size(brief.size ?? ""))}
            />
          </li>
        ) : null}
      </ul>
    </div>
  );
}
