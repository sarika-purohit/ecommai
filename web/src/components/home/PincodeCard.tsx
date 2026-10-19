"use client";

import { useId, useState, type FormEvent, type RefObject } from "react";
import { PinIcon } from "@/components/icons";
import { isValidPincode } from "@/lib/format";
import { pincodeStore } from "@/lib/session";
import { strings } from "@/lib/strings";

const text = strings.pincode;

interface PincodeCardProps {
  pincode: string | null;
  inputRef: RefObject<HTMLInputElement | null>;
}

export function PincodeCard({ pincode, inputRef }: PincodeCardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [invalid, setInvalid] = useState(false);
  const inputId = useId();
  const errorId = useId();

  function save(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    if (!isValidPincode(value)) {
      setInvalid(true);
      inputRef.current?.focus();
      return;
    }
    pincodeStore.set(value);
    setInvalid(false);
    setEditing(false);
  }

  if (pincode && !editing) {
    return (
      <p className="flex flex-wrap items-center gap-x-2 text-ink-soft">
        <PinIcon className="size-5 text-indigo" />
        <span>{text.deliveringTo(pincode)}</span>
        <button
          type="button"
          className="link"
          onClick={() => {
            setDraft(pincode);
            setEditing(true);
          }}
        >
          {text.change}
        </button>
      </p>
    );
  }

  const error = invalid ? text.invalid : null;

  return (
    <form onSubmit={save} className="panel p-5" noValidate>
      <h2 className="display text-[1.5rem]">{text.title}</h2>
      <p className="mt-1 text-ink-soft">{text.body}</p>
      <label htmlFor={inputId} className="mt-4 block font-semibold">
        {text.label}
      </label>
      <div className="mt-1.5 flex gap-2">
        <input
          ref={inputRef}
          id={inputId}
          className="field max-w-[10rem] tracking-[0.12em] tabular-nums"
          inputMode="numeric"
          autoComplete="postal-code"
          maxLength={6}
          pattern="[1-9][0-9]{5}"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value.replace(/\D/g, "").slice(0, 6));
            setInvalid(false);
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
        <button type="submit" className="btn btn-primary btn-small min-h-[3.25rem] flex-1">
          {text.save}
        </button>
      </div>
      {error ? (
        <p id={errorId} role="alert" className="field-error mt-2">
          {error}
        </p>
      ) : null}
    </form>
  );
}
