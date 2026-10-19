"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type HTMLAttributes } from "react";
import { attributeName } from "@/lib/labels";
import { strings } from "@/lib/strings";
import type { DetailValue, MustHave, Rule } from "@/lib/types";

const text = strings.understood;

function EditorActions({ busy, submitLabel, onCancel }: { busy: boolean; submitLabel: string; onCancel: () => void }) {
  return (
    <div className="mt-4 flex gap-2">
      <button type="submit" className="btn btn-primary btn-small flex-1" disabled={busy}>
        {submitLabel}
      </button>
      <button type="button" className="btn btn-secondary btn-small" onClick={onCancel} disabled={busy}>
        {strings.common.cancel}
      </button>
    </div>
  );
}

function useFocusOnMount<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return ref;
}

type ValueKind = "presence" | "number" | "text";

function kindOf(mustHave: MustHave): ValueKind {
  if (mustHave.rule === "present" || mustHave.rule === "absent" || typeof mustHave.value === "boolean") return "presence";
  if (mustHave.rule === "at_most" || mustHave.rule === "at_least" || typeof mustHave.value === "number") return "number";
  return "text";
}

const RULES_FOR: Record<ValueKind, [Rule, Rule]> = {
  presence: ["present", "absent"],
  number: ["at_most", "at_least"],
  text: ["is", "is_not"],
};

function startingRule(mustHave: MustHave, kind: ValueKind): Rule {
  if (kind !== "presence") return mustHave.rule;
  const wanted = Boolean(mustHave.value) === (mustHave.rule !== "absent");
  return wanted ? "present" : "absent";
}

interface MustHaveEditorProps {
  mustHave: MustHave;
  busy: boolean;
  onSave: (rule: Rule, value: DetailValue) => void;
  onCancel: () => void;
}

export function MustHaveEditor({ mustHave, busy, onSave, onCancel }: MustHaveEditorProps) {
  const kind = kindOf(mustHave);
  const [rule, setRule] = useState<Rule>(() => startingRule(mustHave, kind));
  const [value, setValue] = useState(kind === "presence" ? "" : String(mustHave.value));
  const [invalid, setInvalid] = useState(false);
  const groupName = useId();
  const valueId = useId();
  const firstChoice = useFocusOnMount<HTMLInputElement>();
  const name = attributeName(mustHave.attribute);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (kind === "presence") {
      onSave(rule, true);
      return;
    }
    const trimmed = value.trim();
    const parsed = kind === "number" ? Number(trimmed) : trimmed;
    if (trimmed === "" || (kind === "number" && !Number.isFinite(parsed))) {
      setInvalid(true);
      return;
    }
    onSave(rule, parsed);
  }

  return (
    <form onSubmit={submit} className="panel w-full p-4" noValidate>
      <fieldset>
        <legend className="eyebrow mb-2">{name}</legend>
        <div className="segmented">
          {RULES_FOR[kind].map((choice, index) => (
            <span key={choice} className="contents">
              <input
                ref={index === 0 ? firstChoice : undefined}
                type="radio"
                id={`${groupName}-${choice}`}
                name={groupName}
                value={choice}
                checked={rule === choice}
                onChange={() => setRule(choice)}
              />
              <label htmlFor={`${groupName}-${choice}`}>{text.ruleChoices[choice]}</label>
            </span>
          ))}
        </div>
      </fieldset>
      {kind !== "presence" ? (
        <div className="mt-3">
          <label htmlFor={valueId} className="block font-semibold">
            {text.valueLabel(name)}
          </label>
          <input
            id={valueId}
            className="field mt-1.5"
            value={value}
            inputMode={kind === "number" ? "decimal" : undefined}
            aria-invalid={invalid ? true : undefined}
            onChange={(event) => {
              setValue(event.target.value);
              setInvalid(false);
            }}
          />
        </div>
      ) : null}
      <EditorActions busy={busy} submitLabel={strings.common.save} onCancel={onCancel} />
    </form>
  );
}

interface FieldEditorProps {
  label: string;
  initial: string;
  submitLabel?: string;
  placeholder?: string;
  invalidMessage?: string;
  inputProps?: Pick<HTMLAttributes<HTMLInputElement>, "inputMode"> & {
    type?: string;
    min?: string;
    maxLength?: number;
    autoComplete?: string;
  };
  /** Returns the cleaned value, or null when it isn't acceptable. */
  parse: (raw: string) => string | null;
  busy: boolean;
  onSave: (value: string) => void;
  onCancel: () => void;
}

export function FieldEditor({
  label,
  initial,
  submitLabel = strings.common.save,
  placeholder,
  invalidMessage,
  inputProps,
  parse,
  busy,
  onSave,
  onCancel,
}: FieldEditorProps) {
  const [value, setValue] = useState(initial);
  const [invalid, setInvalid] = useState(false);
  const inputId = useId();
  const errorId = useId();
  const input = useFocusOnMount<HTMLInputElement>();

  function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = parse(value);
    if (parsed === null) {
      setInvalid(true);
      input.current?.focus();
      return;
    }
    onSave(parsed);
  }

  return (
    <form onSubmit={submit} className="panel w-full p-4" noValidate>
      <label htmlFor={inputId} className="block font-semibold">
        {label}
      </label>
      <input
        ref={input}
        id={inputId}
        className="field mt-1.5"
        value={value}
        placeholder={placeholder}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={invalid && invalidMessage ? errorId : undefined}
        onChange={(event) => {
          setValue(event.target.value);
          setInvalid(false);
        }}
        {...inputProps}
      />
      {invalid && invalidMessage ? (
        <p id={errorId} role="alert" className="field-error mt-2">
          {invalidMessage}
        </p>
      ) : null}
      <EditorActions busy={busy} submitLabel={submitLabel} onCancel={onCancel} />
    </form>
  );
}
