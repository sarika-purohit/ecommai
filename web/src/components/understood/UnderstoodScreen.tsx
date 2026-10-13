"use client";

import { useRouter } from "next/navigation";
import { useId, useState, type ReactNode } from "react";
import { AppHeader } from "@/components/AppHeader";
import { EditableTag, focusEditKey } from "@/components/EditableTag";
import {
  ArrowRightIcon,
  CalendarIcon,
  InfoIcon,
  LockIcon,
  PhotoIcon,
  PinIcon,
  PlusIcon,
  RulerIcon,
  RupeeIcon,
} from "@/components/icons";
import { NoSession, ScreenPlaceholder } from "@/components/NoSession";
import { Tag } from "@/components/Tag";
import { FieldEditor, MustHaveEditor } from "@/components/understood/Editors";
import { QuestionCard } from "@/components/understood/QuestionCard";
import { updateBrief } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { isValidPincode, rupees, shortDay, todayInIndia } from "@/lib/format";
import { categoryLabel, mustHaveLabel, niceToHaveLabel } from "@/lib/labels";
import { openQuestion, sameRule, sessionStore, updateSession, type SearchSession } from "@/lib/session";
import { useIsClient } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { BriefChange, MustHave } from "@/lib/types";

const text = strings.understood;
const labels = strings.labels;

type Status = { kind: "saving" } | { kind: "saved" } | { kind: "error"; message: string } | null;

export function UnderstoodScreen() {
  const isClient = useIsClient();
  const session = sessionStore.use();

  return (
    <>
      <AppHeader back={{ href: "/", label: text.back }} />
      <main className="shell pb-36 pt-2">
        {!isClient ? <ScreenPlaceholder /> : session ? <Checklist session={session} /> : <NoSession />}
      </main>
    </>
  );
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

function Checklist({ session }: { session: SearchSession }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>(null);
  const [editing, setEditing] = useState<string | null>(null);

  const { brief } = session;
  const own = brief.must_haves.filter((m) => !m.from_rule);
  const rulesOn = brief.must_haves.filter((m) => m.from_rule);
  const rulesOff = session.rulesOff.filter((off) => !rulesOn.some((on) => sameRule(on, off)));
  const question = openQuestion(session);

  async function apply(change: BriefChange, focusAfter: string, after?: (next: SearchSession) => SearchSession) {
    setBusy(true);
    setStatus({ kind: "saving" });
    try {
      const { brief: updated } = await updateBrief(session.sessionId, change);
      updateSession((current) => {
        const next = { ...current, brief: updated };
        return after ? after(next) : next;
      });
      setEditing(null);
      setStatus({ kind: "saved" });
      focusEditKey(focusAfter);
    } catch (caught) {
      setStatus({ kind: "error", message: errorMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  function close(key: string) {
    setEditing(null);
    focusEditKey(key);
  }

  function switchRule(mustHave: MustHave, enabled: boolean) {
    void apply({ type: "set_rule", must_have: mustHave, enabled }, `rule:${mustHave.attribute}:${mustHave.value}`, (next) => ({
      ...next,
      rulesOff: enabled
        ? next.rulesOff.filter((off) => !sameRule(off, mustHave))
        : [...next.rulesOff.filter((off) => !sameRule(off, mustHave)), mustHave],
    }));
  }

  function findMatches() {
    updateSession((current) => ({ ...current, searchRun: current.searchRun + 1 }));
    router.push("/results");
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="display text-[2.25rem] sm:text-[2.75rem]">{text.title}</h1>
        <div className="panel mt-4 p-4">
          <p className="eyebrow">{text.youAsked}</p>
          {session.requestText ? (
            <p className="mt-1 text-[1.25rem] leading-snug">{text.quoted(session.requestText)}</p>
          ) : null}
          <p className="mt-2 font-semibold text-indigo">{categoryLabel(brief)}</p>
          {session.hasPhoto || brief.reference_image_url ? (
            <div className="mt-3">
              <Tag icon={<PhotoIcon className="size-[1.125rem]" />}>
                <span className="tag-text">{text.withPhoto}</span>
              </Tag>
            </div>
          ) : null}
          {brief.not_checkable?.length ? (
            <p className="mt-3 flex gap-2 text-base text-ink-soft">
              <InfoIcon className="mt-0.5 size-5 flex-none text-indigo" />
              {strings.results.notCheckable(brief.not_checkable)}
            </p>
          ) : null}
        </div>
      </div>

      {question ? (
        <QuestionCard
          key={question}
          question={question}
          busy={busy}
          onAnswer={(answer) => void apply({ type: "answer", question, answer }, "add-must-have")}
          onSkip={() => updateSession((current) => ({ ...current, dismissedQuestion: question }))}
        />
      ) : null}

      <Section title={text.mustHaves} hint={text.mustHavesHint}>
        {own.length === 0 ? <p className="text-ink-soft">{text.noMustHaves}</p> : null}
        <ul className="flex flex-wrap gap-2.5">
          {own.map((mustHave) => {
            const key = `must:${mustHave.id}`;
            const label = mustHaveLabel(mustHave);
            return (
              <li key={mustHave.id} className={editing === key ? "basis-full" : "max-w-full"}>
                {editing === key ? (
                  <MustHaveEditor
                    mustHave={mustHave}
                    busy={busy}
                    onCancel={() => close(key)}
                    onSave={(rule, value) =>
                      void apply({ type: "edit_must_have", id: mustHave.id, rule, value }, key)
                    }
                  />
                ) : (
                  <EditableTag
                    label={label}
                    editKey={key}
                    disabled={busy}
                    onEdit={() => setEditing(key)}
                    onRemove={() => void apply({ type: "remove_must_have", id: mustHave.id }, "add-must-have")}
                  />
                )}
              </li>
            );
          })}
          <li className={editing === "add" ? "basis-full" : undefined}>
            {editing === "add" ? (
              <FieldEditor
                label={text.addMustHaveLabel}
                placeholder={text.addMustHavePlaceholder}
                initial=""
                submitLabel={text.add}
                parse={(raw) => (raw.trim() ? raw.trim() : null)}
                busy={busy}
                onCancel={() => close("add-must-have")}
                onSave={(value) => void apply({ type: "add_must_have", text: value }, "add-must-have")}
              />
            ) : (
              <button
                type="button"
                className="add-tag"
                data-edit-key="add-must-have"
                onClick={() => setEditing("add")}
                disabled={busy}
              >
                <PlusIcon className="size-5" />
                {text.addMustHave}
              </button>
            )}
          </li>
        </ul>
      </Section>

      {rulesOn.length + rulesOff.length > 0 ? (
        <Section title={text.rules} hint={text.rulesHint}>
          <ul className="flex flex-col gap-2.5">
            {[...rulesOn.map((m) => ({ mustHave: m, on: true })), ...rulesOff.map((m) => ({ mustHave: m, on: false }))].map(
              ({ mustHave, on }) => {
                const label = mustHaveLabel(mustHave);
                return (
                  <li key={`${mustHave.attribute}:${mustHave.rule}:${String(mustHave.value)}`} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <Tag tone={on ? "rule" : "off"} icon={<LockIcon className="size-[1.125rem]" />}>
                      <span className="tag-text">{label}</span>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={on}
                        aria-label={text.ruleSwitch(label)}
                        className="switch-button"
                        data-edit-key={`rule:${mustHave.attribute}:${mustHave.value}`}
                        disabled={busy}
                        onClick={() => switchRule(mustHave, !on)}
                      >
                        <span className="switch" aria-hidden="true" />
                      </button>
                    </Tag>
                    {on ? null : <span className="text-base font-semibold text-ink-soft">{text.ruleOff}</span>}
                  </li>
                );
              },
            )}
          </ul>
        </Section>
      ) : null}

      {brief.nice_to_haves.length > 0 ? (
        <Section title={text.niceToHaves} hint={text.niceToHavesHint}>
          <ul className="flex flex-wrap gap-2.5">
            {brief.nice_to_haves.map((nice) => (
              <li key={`${nice.attribute}:${String(nice.value)}`} className="max-w-full">
                <EditableTag
                  label={niceToHaveLabel(nice)}
                  editKey={`nice:${nice.attribute}`}
                  disabled={busy}
                  onRemove={() =>
                    void apply({ type: "remove_nice_to_have", attribute: nice.attribute, value: nice.value }, "add-must-have")
                  }
                />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title={text.limits} hint={text.limitsHint}>
        <ul className="flex flex-wrap gap-2.5">
          <LimitItem
            editKey="budget"
            editing={editing}
            setEditing={setEditing}
            busy={busy}
            icon={<RupeeIcon className="size-[1.125rem]" />}
            label={brief.budget_max_inr !== null ? labels.budget(rupees(brief.budget_max_inr)) : null}
            addLabel={labels.addBudget}
            onRemove={() => void apply({ type: "set_budget", budget_max_inr: null }, "budget")}
            editor={
              <FieldEditor
                label={text.budgetLabel}
                initial={brief.budget_max_inr !== null ? String(brief.budget_max_inr) : ""}
                inputProps={{ inputMode: "numeric" }}
                invalidMessage={text.budgetInvalid}
                parse={(raw) => {
                  const amount = Number(raw.replace(/[^\d]/g, ""));
                  return Number.isInteger(amount) && amount > 0 ? String(amount) : null;
                }}
                busy={busy}
                onCancel={() => close("budget")}
                onSave={(value) => void apply({ type: "set_budget", budget_max_inr: Number(value) }, "budget")}
              />
            }
          />
          <LimitItem
            editKey="deadline"
            editing={editing}
            setEditing={setEditing}
            busy={busy}
            icon={<CalendarIcon className="size-[1.125rem]" />}
            label={brief.deadline_date ? labels.deadline(shortDay(brief.deadline_date)) : null}
            addLabel={labels.addDeadline}
            onRemove={() => void apply({ type: "set_deadline", deadline_date: null }, "deadline")}
            editor={
              <FieldEditor
                label={text.deadlineLabel}
                initial={brief.deadline_date ?? ""}
                inputProps={{ type: "date", min: todayInIndia() }}
                parse={(raw) => (/^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : null)}
                busy={busy}
                onCancel={() => close("deadline")}
                onSave={(value) => void apply({ type: "set_deadline", deadline_date: value }, "deadline")}
              />
            }
          />
          <LimitItem
            editKey="pincode"
            editing={editing}
            setEditing={setEditing}
            busy={busy}
            icon={<PinIcon className="size-[1.125rem]" />}
            label={brief.pincode ? labels.pincode(brief.pincode) : null}
            addLabel={labels.addPincode}
            editor={
              <FieldEditor
                label={text.pincodeLabel}
                initial={brief.pincode ?? ""}
                inputProps={{ inputMode: "numeric", maxLength: 6, autoComplete: "postal-code" }}
                invalidMessage={strings.pincode.invalid}
                parse={(raw) => (isValidPincode(raw.trim()) ? raw.trim() : null)}
                busy={busy}
                onCancel={() => close("pincode")}
                onSave={(value) => void apply({ type: "set_pincode", pincode: value }, "pincode")}
              />
            }
          />
          <LimitItem
            editKey="size"
            editing={editing}
            setEditing={setEditing}
            busy={busy}
            icon={<RulerIcon className="size-[1.125rem]" />}
            label={brief.size ? labels.size(brief.size) : null}
            addLabel={labels.addSize}
            onRemove={() => void apply({ type: "set_size", size: null }, "size")}
            editor={
              <FieldEditor
                label={text.sizeLabel}
                initial={brief.size ?? ""}
                parse={(raw) => (raw.trim() ? raw.trim() : null)}
                busy={busy}
                onCancel={() => close("size")}
                onSave={(value) => void apply({ type: "set_size", size: value }, "size")}
              />
            }
          />
        </ul>
      </Section>

      <p role="status" aria-live="polite" className="min-h-6 text-base text-ink-soft">
        {status?.kind === "saving" ? text.saving : status?.kind === "saved" ? text.saved : null}
      </p>
      {status?.kind === "error" ? (
        <p role="alert" className="field-error -mt-5">
          {status.message}
        </p>
      ) : null}

      <div className="fixed inset-x-0 bottom-0 z-10 bg-gradient-to-t from-paper from-70% to-transparent pb-[max(1rem,env(safe-area-inset-bottom))] pt-6">
        <div className="shell">
          <button type="button" className="btn btn-primary w-full" onClick={findMatches} disabled={busy}>
            {text.findMatches}
            <ArrowRightIcon className="size-5" />
          </button>
        </div>
      </div>
    </div>
  );
}

interface LimitItemProps {
  editKey: string;
  editing: string | null;
  setEditing: (key: string) => void;
  busy: boolean;
  icon: ReactNode;
  /** null when the limit isn't set yet. */
  label: string | null;
  addLabel: string;
  onRemove?: () => void;
  editor: ReactNode;
}

function LimitItem({ editKey, editing, setEditing, busy, icon, label, addLabel, onRemove, editor }: LimitItemProps) {
  if (editing === editKey) return <li className="basis-full">{editor}</li>;
  if (label === null) {
    return (
      <li>
        <button
          type="button"
          className="add-tag"
          data-edit-key={editKey}
          onClick={() => setEditing(editKey)}
          disabled={busy}
        >
          <PlusIcon className="size-5" />
          {addLabel}
        </button>
      </li>
    );
  }
  return (
    <li className="max-w-full">
      <EditableTag
        label={label}
        editKey={editKey}
        icon={icon}
        disabled={busy}
        onEdit={() => setEditing(editKey)}
        onRemove={onRemove}
      />
    </li>
  );
}
