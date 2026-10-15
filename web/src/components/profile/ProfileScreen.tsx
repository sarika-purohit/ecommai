"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AppHeader } from "@/components/AppHeader";
import { EditableTag, focusEditKey } from "@/components/EditableTag";
import { LockIcon, PinIcon, PlusIcon } from "@/components/icons";
import { ScreenPlaceholder } from "@/components/NoSession";
import { RuleEditor, type RuleDraft } from "@/components/profile/RuleEditor";
import { FieldEditor } from "@/components/understood/Editors";
import { clearTaste, deleteMe, getMe, saveRules, updateMe } from "@/lib/api";
import { forgetDevice, signInRequired, signOut, useAuth } from "@/lib/auth";
import { newChat } from "@/lib/chat";
import { canRetry, errorMessage } from "@/lib/errors";
import { isValidPincode } from "@/lib/format";
import { mustHaveLabel } from "@/lib/labels";
import { pincodeStore } from "@/lib/session";
import { useIsClient } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { Me, MustHave } from "@/lib/types";

const text = strings.profile;

const SUGGESTED_RULES: RuleDraft[] = [
  { attribute: "fabric", rule: "is_not", value: "polyester" },
  { attribute: "cod", rule: "present", value: true },
  { attribute: "returnable", rule: "present", value: true },
];

type Load = { status: "loading" } | { status: "ready"; me: Me } | { status: "error"; message: string; retryable: boolean };
type Note = { kind: "saving" } | { kind: "done"; message: string } | { kind: "error"; message: string } | null;

export function ProfileScreen() {
  const isClient = useIsClient();
  const [attempt, setAttempt] = useState(0);
  return (
    <>
      <AppHeader back={{ href: "/", label: text.back }} />
      <main className="shell pb-16 pt-2">
        {isClient ? <ProfileLoader key={attempt} onRetry={() => setAttempt((n) => n + 1)} /> : <ScreenPlaceholder />}
      </main>
    </>
  );
}

function ProfileLoader({ onRetry }: { onRetry: () => void }) {
  const [load, setLoad] = useState<Load>({ status: "loading" });

  useEffect(() => {
    let live = true;
    getMe().then(
      (me) => live && setLoad({ status: "ready", me }),
      (error: unknown) => live && setLoad({ status: "error", message: errorMessage(error), retryable: canRetry(error) }),
    );
    return () => {
      live = false;
    };
  }, []);

  if (load.status === "loading") return <ScreenPlaceholder />;
  if (load.status === "error") {
    return (
      <div role="alert" className="panel p-5">
        <p className="font-semibold text-fail">{load.message}</p>
        {load.retryable ? (
          <button type="button" className="btn btn-primary btn-small mt-4" onClick={onRetry}>
            {strings.common.tryAgain}
          </button>
        ) : null}
      </div>
    );
  }
  return <Profile me={load.me} />;
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <hr className="stitch" />
      <div>
        <h2 id={id} className="eyebrow pt-2">
          {title}
        </h2>
        {hint ? <p className="mt-1 text-base text-ink-soft">{hint}</p> : null}
      </div>
      {children}
    </section>
  );
}

function NoteLine({ note }: { note: Note }) {
  return (
    <>
      <p role="status" className="text-base text-ink-soft">
        {note?.kind === "saving" ? text.saving : note?.kind === "done" ? note.message : null}
      </p>
      {note?.kind === "error" ? (
        <p role="alert" className="field-error">
          {note.message}
        </p>
      ) : null}
    </>
  );
}

function Profile({ me }: { me: Me }) {
  const auth = useAuth();
  const user = auth.status === "signed-in" ? auth.user : null;
  const name = user?.name ?? me.name;
  const email = user?.email ?? me.email;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display text-[2.25rem] sm:text-[2.75rem]">{text.title}</h1>
        {!signInRequired ? null : name || email ? (
          <p className="mt-2">
            {name ? <span className="block font-semibold">{name}</span> : null}
            {email ? <span className="block text-ink-soft">{email}</span> : null}
          </p>
        ) : (
          <p className="mt-2 text-ink-soft">{strings.account.demoUser}</p>
        )}
      </div>
      <PincodeSection saved={me.pincode} />
      <RulesSection initial={me.rules} />
      <TasteSection initial={me.has_taste} />
      <DataSection />
      {signInRequired ? (
        <button type="button" className="btn btn-secondary w-full" onClick={() => void leave(false)}>
          {text.signOut}
        </button>
      ) : null}
    </div>
  );
}

/** Signs out, after forgetting this device's copy of the search and chat (and the pincode, when the data was deleted). */
async function leave(deleted: boolean) {
  newChat();
  if (deleted) pincodeStore.set(null);
  if (!signInRequired) {
    forgetDevice();
    window.location.reload();
    return;
  }
  await signOut();
}

function PincodeSection({ saved }: { saved: string | null }) {
  const local = pincodeStore.use();
  const pincode = local ?? saved;
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);

  useEffect(() => {
    if (saved && !pincodeStore.get()) pincodeStore.set(saved);
  }, [saved]);

  async function save(value: string) {
    setBusy(true);
    setNote({ kind: "saving" });
    try {
      await updateMe({ pincode: value });
      pincodeStore.set(value);
      setEditing(false);
      setNote({ kind: "done", message: text.pincodeSaved });
      focusEditKey("pincode");
    } catch (caught) {
      setNote({ kind: "error", message: errorMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title={text.pincodeTitle} hint={text.pincodeHint}>
      {editing ? (
        <FieldEditor
          label={strings.pincode.label}
          initial={pincode ?? ""}
          inputProps={{ inputMode: "numeric", maxLength: 6, autoComplete: "postal-code" }}
          invalidMessage={strings.pincode.invalid}
          parse={(raw) => (isValidPincode(raw.trim()) ? raw.trim() : null)}
          busy={busy}
          onCancel={() => {
            setEditing(false);
            focusEditKey("pincode");
          }}
          onSave={(value) => void save(value)}
        />
      ) : (
        <p className="flex flex-wrap items-center gap-x-2">
          <PinIcon className="size-5 text-indigo" />
          <span className="font-semibold">{pincode ? strings.pincode.deliveringTo(pincode) : text.noPincode}</span>
          <button type="button" className="link" data-edit-key="pincode" onClick={() => setEditing(true)}>
            {strings.pincode.change}
          </button>
        </p>
      )}
      <NoteLine note={note} />
    </Section>
  );
}

function nextRuleId(rules: MustHave[]): string {
  let n = 1;
  while (rules.some((rule) => rule.id === `r${n}`)) n += 1;
  return `r${n}`;
}

function RulesSection({ initial }: { initial: MustHave[] }) {
  const [rules, setRules] = useState<MustHave[]>(initial);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const suggestions = SUGGESTED_RULES.filter((s) => !rules.some((rule) => rule.attribute === s.attribute));

  async function save(next: MustHave[], focusAfter: string) {
    setBusy(true);
    setNote({ kind: "saving" });
    try {
      setRules(await saveRules(next));
      setEditing(null);
      setNote({ kind: "done", message: text.rulesSaved });
      focusEditKey(focusAfter);
    } catch (caught) {
      setNote({ kind: "error", message: errorMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  function add(draft: RuleDraft) {
    void save([...rules, { ...draft, id: nextRuleId(rules), from_rule: true }], "add-rule");
  }

  return (
    <Section title={text.rulesTitle} hint={text.rulesHint}>
      {rules.length === 0 ? <p className="text-ink-soft">{text.noRules}</p> : null}
      <ul className="flex flex-wrap gap-2.5">
        {rules.map((rule) => {
          const key = `rule:${rule.id}`;
          const label = mustHaveLabel(rule);
          return (
            <li key={rule.id} className={editing === key ? "basis-full" : "max-w-full"}>
              {editing === key ? (
                <RuleEditor
                  initial={rule}
                  rules={rules}
                  busy={busy}
                  onCancel={() => {
                    setEditing(null);
                    focusEditKey(key);
                  }}
                  onSave={(draft) => void save(rules.map((r) => (r.id === rule.id ? { ...r, ...draft } : r)), key)}
                />
              ) : (
                <EditableTag
                  label={label}
                  editKey={key}
                  tone="rule"
                  icon={<LockIcon className="size-[1.125rem]" />}
                  disabled={busy}
                  onEdit={() => setEditing(key)}
                  onRemove={() => void save(rules.filter((r) => r.id !== rule.id), "add-rule")}
                />
              )}
            </li>
          );
        })}
        <li className={editing === "new" ? "basis-full" : undefined}>
          {editing === "new" ? (
            <RuleEditor
              initial={null}
              rules={rules}
              busy={busy}
              onCancel={() => {
                setEditing(null);
                focusEditKey("add-rule");
              }}
              onSave={add}
            />
          ) : (
            <button type="button" className="add-tag" data-edit-key="add-rule" onClick={() => setEditing("new")} disabled={busy}>
              <PlusIcon className="size-5" />
              {text.addRule}
            </button>
          )}
        </li>
      </ul>
      {suggestions.length > 0 && editing === null ? (
        <div>
          <p className="text-base font-semibold text-ink-soft">{text.suggestions}</p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {suggestions.map((draft) => {
              const label = mustHaveLabel(draft);
              return (
                <li key={draft.attribute}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-small gap-1.5 rounded-full"
                    aria-label={text.addSuggestion(label)}
                    onClick={() => add(draft)}
                    disabled={busy}
                  >
                    <PlusIcon className="size-4" />
                    {label}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      <NoteLine note={note} />
    </Section>
  );
}

function TasteSection({ initial }: { initial: boolean }) {
  const [hasTaste, setHasTaste] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);

  async function clear() {
    setBusy(true);
    setNote({ kind: "saving" });
    try {
      await clearTaste();
      setHasTaste(false);
      setNote({ kind: "done", message: text.tasteCleared });
    } catch (caught) {
      setNote({ kind: "error", message: errorMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title={text.tasteTitle}>
      <p>{hasTaste ? text.tasteOn : text.tasteOff}</p>
      {hasTaste ? (
        <button type="button" className="btn btn-secondary btn-small self-start" onClick={() => void clear()} disabled={busy}>
          {text.clearTaste}
        </button>
      ) : null}
      <NoteLine note={note} />
    </Section>
  );
}

function DataSection() {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirmHeading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (confirming) confirmHeading.current?.focus();
  }, [confirming]);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteMe();
      await leave(true);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <Section title={text.dataTitle} hint={text.dataHint}>
      {confirming ? (
        <div role="group" aria-labelledby={titleId} className="rounded-[1.25rem] bg-fail-wash p-5 ring-2 ring-fail">
          <h3 id={titleId} ref={confirmHeading} tabIndex={-1} className="display text-[1.5rem] focus:outline-none">
            {text.confirmTitle}
          </h3>
          <p className="mt-2">{text.confirmBody}</p>
          {error ? (
            <p role="alert" className="field-error mt-2">
              {error}
            </p>
          ) : null}
          <div className="mt-4 flex flex-col gap-2">
            <button type="button" className="btn btn-danger w-full" onClick={() => void remove()} disabled={busy} aria-busy={busy}>
              {busy ? text.deleting : text.confirmDelete}
            </button>
            <button
              type="button"
              className="btn btn-secondary w-full"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                window.setTimeout(() => opener.current?.focus(), 0);
              }}
            >
              {strings.common.cancel}
            </button>
          </div>
        </div>
      ) : (
        <button
          ref={opener}
          type="button"
          className="btn btn-secondary btn-small self-start border-fail text-fail hover:border-fail"
          onClick={() => setConfirming(true)}
        >
          {text.deleteData}
        </button>
      )}
    </Section>
  );
}
