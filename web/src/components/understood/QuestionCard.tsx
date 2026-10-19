"use client";

import { useId, useState, type FormEvent } from "react";
import { QuestionIcon } from "@/components/icons";
import { strings } from "@/lib/strings";

const text = strings.understood;

interface QuestionCardProps {
  question: string;
  busy: boolean;
  onAnswer: (answer: string) => void;
  onSkip: () => void;
}

export function QuestionCard({ question, busy, onAnswer, onSkip }: QuestionCardProps) {
  const [answer, setAnswer] = useState("");
  const questionId = useId();
  const inputId = useId();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (answer.trim()) onAnswer(answer.trim());
  }

  return (
    <section aria-labelledby={questionId} className="rounded-[1.25rem] bg-marigold-wash p-5 ring-2 ring-marigold">
      <p className="eyebrow flex items-center gap-1.5 text-ink">
        <QuestionIcon className="size-5" />
        {text.question}
      </p>
      <h2 id={questionId} className="display mt-2 text-[1.625rem]">
        {question}
      </h2>
      <form onSubmit={submit} className="mt-4" noValidate>
        <label htmlFor={inputId} className="sr-only">
          {text.answerLabel}
        </label>
        <div className="flex gap-2">
          <input
            id={inputId}
            className="field flex-1"
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder={text.answerLabel}
            autoComplete="off"
          />
          <button type="submit" className="btn btn-primary btn-small min-h-[3.25rem]" disabled={busy || !answer.trim()}>
            {text.answer}
          </button>
        </div>
      </form>
      <button type="button" className="link mt-1" onClick={onSkip} disabled={busy}>
        {text.skip}
      </button>
    </section>
  );
}
