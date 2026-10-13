"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { LockIcon } from "@/components/icons";
import { Tag } from "@/components/Tag";
import { attributeName, mustHaveLabel } from "@/lib/labels";
import { strings } from "@/lib/strings";
import type { MustHave, Rule } from "@/lib/types";
import { RULE_DETAILS, ruleDetail, type RuleDetail } from "@/lib/vocabulary";

const text = strings.profile;
const ruleChoices = strings.understood.ruleChoices;

export type RuleDraft = Omit<MustHave, "id" | "from_rule">;

const NEW_RULE: RuleDraft = { attribute: "fabric", rule: "is_not", value: "polyester" };

function detailFor(attribute: string, value: MustHave["value"] | undefined): RuleDetail {
  return ruleDetail(attribute) ?? { attribute, kind: typeof value === "boolean" ? "presence" : "text" };
}

function startingDraft(detail: RuleDetail): RuleDraft {
  if (detail.kind === "presence") return { attribute: detail.attribute, rule: "present", value: true };
  return { attribute: detail.attribute, rule: "is", value: detail.values?.[0] ?? "" };
}

interface RuleEditorProps {
  /** The rule being edited, or null to add one. */
  initial: MustHave | null;
  /** Every saved rule, to stop two rules on the same detail. */
  rules: MustHave[];
  busy: boolean;
  onSave: (draft: RuleDraft) => void;
  onCancel: () => void;
}

export function RuleEditor({ initial, rules, busy, onSave, onCancel }: RuleEditorProps) {
  const [draft, setDraft] = useState<RuleDraft>(() =>
    initial ? { attribute: initial.attribute, rule: initial.rule, value: initial.value } : NEW_RULE,
  );
  const [duplicate, setDuplicate] = useState(false);
  const detailId = useId();
  const valueId = useId();
  const ruleName = useId();
  const errorId = useId();
  const firstField = useRef<HTMLSelectElement>(null);
  const detail = detailFor(draft.attribute, draft.value);
  const choices: [Rule, Rule] = detail.kind === "presence" ? ["present", "absent"] : ["is", "is_not"];
  const options = RULE_DETAILS.some((d) => d.attribute === draft.attribute) ? RULE_DETAILS : [detail, ...RULE_DETAILS];
  const values = detail.values ?? (detail.kind === "text" ? [String(draft.value)] : []);

  useEffect(() => {
    firstField.current?.focus();
  }, []);

  function changeDetail(attribute: string) {
    setDraft(startingDraft(detailFor(attribute, undefined)));
    setDuplicate(false);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (rules.some((rule) => rule.attribute === draft.attribute && rule.id !== initial?.id)) {
      setDuplicate(true);
      return;
    }
    onSave(detail.kind === "presence" ? { ...draft, value: true } : draft);
  }

  return (
    <form onSubmit={submit} className="panel w-full p-4" noValidate>
      <label htmlFor={detailId} className="block font-semibold">
        {text.detailLabel}
      </label>
      <select
        ref={firstField}
        id={detailId}
        className="field mt-1.5"
        value={draft.attribute}
        aria-invalid={duplicate || undefined}
        aria-describedby={duplicate ? errorId : undefined}
        onChange={(event) => changeDetail(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.attribute} value={option.attribute}>
            {attributeName(option.attribute)}
          </option>
        ))}
      </select>
      {duplicate ? (
        <p id={errorId} role="alert" className="field-error mt-2">
          {text.duplicateRule}
        </p>
      ) : null}

      <fieldset className="mt-4">
        <legend className="sr-only">{attributeName(draft.attribute)}</legend>
        <div className="segmented">
          {choices.map((choice) => (
            <span key={choice} className="contents">
              <input
                type="radio"
                id={`${ruleName}-${choice}`}
                name={ruleName}
                value={choice}
                checked={draft.rule === choice}
                onChange={() => setDraft((current) => ({ ...current, rule: choice }))}
              />
              <label htmlFor={`${ruleName}-${choice}`}>{ruleChoices[choice]}</label>
            </span>
          ))}
        </div>
      </fieldset>

      {detail.kind === "text" ? (
        <div className="mt-3">
          <label htmlFor={valueId} className="block font-semibold">
            {text.valueLabel}
          </label>
          <select
            id={valueId}
            className="field mt-1.5"
            value={String(draft.value)}
            onChange={(event) => setDraft((current) => ({ ...current, value: event.target.value }))}
          >
            {values.map((value) => (
              <option key={value} value={value}>
                {mustHaveLabel({ attribute: draft.attribute, rule: "is", value })}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="text-base text-ink-soft">{text.preview}</span>
        <Tag tone="rule" icon={<LockIcon className="size-[1.125rem]" />}>
          <span className="tag-text">{mustHaveLabel(draft)}</span>
        </Tag>
      </div>

      <div className="mt-4 flex gap-2">
        <button type="submit" className="btn btn-primary btn-small flex-1" disabled={busy}>
          {strings.common.save}
        </button>
        <button type="button" className="btn btn-secondary btn-small" onClick={onCancel} disabled={busy}>
          {strings.common.cancel}
        </button>
      </div>
    </form>
  );
}
