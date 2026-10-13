"use client";

import { useId } from "react";
import { strings } from "@/lib/strings";
import type { SearchSource } from "@/lib/types";

const text = strings.source;
const SOURCES: SearchSource[] = ["live", "catalog"];

interface SourceChoiceProps {
  value: SearchSource;
  onChange: (source: SearchSource) => void;
  disabled?: boolean;
  /** Lets focus come back to the chosen option after the checklist updates. */
  editKey?: string;
}

/** Live stores or the demo catalogue, with one line saying what the chosen one means. */
export function SourceChoice({ value, onChange, disabled, editKey }: SourceChoiceProps) {
  const name = useId();
  const helpId = useId();
  return (
    <fieldset aria-describedby={helpId} disabled={disabled}>
      <legend className="sr-only">{text.legend}</legend>
      <div className="segmented">
        {SOURCES.map((choice) => (
          <span key={choice} className="contents">
            <input
              type="radio"
              id={`${name}-${choice}`}
              name={name}
              value={choice}
              checked={value === choice}
              data-edit-key={editKey && value === choice ? editKey : undefined}
              onChange={() => onChange(choice)}
            />
            <label htmlFor={`${name}-${choice}`}>{text[choice]}</label>
          </span>
        ))}
      </div>
      <p id={helpId} className="mt-2 text-base leading-snug text-ink-soft">
        {text.help[value]}
      </p>
    </fieldset>
  );
}
