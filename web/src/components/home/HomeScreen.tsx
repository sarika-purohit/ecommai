"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { AppHeader } from "@/components/AppHeader";
import { PincodeCard } from "@/components/home/PincodeCard";
import { ArrowRightIcon, CameraIcon, CloseIcon } from "@/components/icons";
import { createBrief } from "@/lib/api";
import { errorMessage } from "@/lib/errors";
import { pincodeStore, sessionStore } from "@/lib/session";
import { strings } from "@/lib/strings";

const text = strings.home;

interface Photo {
  file: File;
  url: string;
}

type Example = (typeof text.examples)[number];

export function HomeScreen() {
  const router = useRouter();
  const pincode = pincodeStore.use();
  const [request, setRequest] = useState("");
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [waitingForPhoto, setWaitingForPhoto] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pincodeRequired, setPincodeRequired] = useState(false);

  const requestId = useId();
  const hintId = useId();
  const errorId = useId();
  const requestRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pincodeRef = useRef<HTMLInputElement>(null);
  const photoUrl = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      if (photoUrl.current) URL.revokeObjectURL(photoUrl.current);
    };
  }, []);

  function replacePhoto(next: Photo | null) {
    if (photoUrl.current) URL.revokeObjectURL(photoUrl.current);
    photoUrl.current = next?.url ?? null;
    setPhoto(next);
  }

  async function start(requestText: string, image: File | null) {
    const trimmed = requestText.trim();
    if (!pincode) {
      setPincodeRequired(true);
      pincodeRef.current?.focus();
      return;
    }
    if (!trimmed && !image) {
      setError(text.emptyRequest);
      requestRef.current?.focus();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await createBrief({
        text: trimmed || undefined,
        image: image ?? undefined,
        pincode,
        language: strings.language,
      });
      sessionStore.set({
        sessionId: response.session_id,
        requestText: trimmed,
        hasPhoto: Boolean(image),
        brief: { ...response.brief, question: response.question ?? response.brief.question ?? null },
        dismissedQuestion: null,
        rulesOff: [],
        searchRun: 0,
      });
      router.push("/understood");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void start(request, photo?.file ?? null);
  }

  function runExample(example: Example) {
    setRequest(example.text);
    setError(null);
    if (example.needsPhoto && !photo) {
      setWaitingForPhoto(example.text);
      setError(text.needPhoto);
      fileRef.current?.click();
      return;
    }
    void start(example.text, photo?.file ?? null);
  }

  function choosePhoto(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    event.target.value = "";
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError(text.photoUnreadable);
      return;
    }
    replacePhoto({ file, url: URL.createObjectURL(file) });
    setError(null);
    if (waitingForPhoto !== null) {
      const pending = waitingForPhoto;
      setWaitingForPhoto(null);
      void start(pending, file);
    }
  }

  return (
    <>
      <AppHeader />
      <main className="shell flex flex-col gap-6 pb-10 pt-2">
        {!pincode ? <PincodeCard pincode={null} inputRef={pincodeRef} required={pincodeRequired} /> : null}

        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <h1 className="display text-[2.25rem] sm:text-[2.75rem]">
            <label htmlFor={requestId}>{text.title}</label>
          </h1>
          <p id={hintId} className="-mt-2 text-ink-soft">
            {text.hint}
          </p>

          <div className="panel overflow-hidden shadow-none ring-2 ring-line-strong transition focus-within:ring-[3px] focus-within:ring-indigo">
            <textarea
              ref={requestRef}
              id={requestId}
              className="block min-h-[9.5rem] w-full resize-none bg-transparent px-[1.125rem] pt-4 text-[1.25rem] leading-[1.45] placeholder:text-ink-soft/70 focus:outline-none"
              placeholder={text.placeholder}
              value={request}
              onChange={(event) => setRequest(event.target.value)}
              aria-describedby={error ? `${hintId} ${errorId}` : hintId}
              aria-invalid={error === text.emptyRequest ? true : undefined}
            />

            {photo ? (
              <div className="mx-3 mb-1 flex items-center gap-3 rounded-xl bg-wash p-2">
                <span className="relative size-16 flex-none overflow-hidden rounded-lg bg-card">
                  <Image src={photo.url} alt={text.photoAlt} fill unoptimized sizes="64px" className="object-cover" />
                </span>
                <span className="min-w-0 flex-1 font-semibold">{text.photoAdded}</span>
                <button
                  type="button"
                  className="flex size-11 flex-none items-center justify-center rounded-full text-ink hover:bg-card"
                  onClick={() => replacePhoto(null)}
                  aria-label={text.removePhoto}
                >
                  <CloseIcon className="size-5" />
                </button>
              </div>
            ) : null}

            <div className="flex items-center gap-2 px-3 pb-3 pt-1">
              <button
                type="button"
                className="btn btn-small gap-2 bg-wash text-indigo hover:bg-line"
                onClick={() => fileRef.current?.click()}
              >
                <CameraIcon className="size-5" />
                {photo ? text.changePhoto : text.addPhoto}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
                onChange={choosePhoto}
              />
            </div>
          </div>

          {error ? (
            <p id={errorId} role="alert" className="field-error -mt-1">
              {error}
            </p>
          ) : null}

          <button type="submit" className="btn btn-primary w-full" disabled={submitting} aria-busy={submitting}>
            {submitting ? text.submitting : text.submit}
            {submitting ? null : <ArrowRightIcon className="size-5" />}
          </button>
        </form>

        {pincode ? (
          <div className="-mt-3">
            <PincodeCard pincode={pincode} inputRef={pincodeRef} required={false} />
          </div>
        ) : null}

        <section aria-labelledby="examples-title" className="flex flex-col gap-3">
          <hr className="stitch" />
          <h2 id="examples-title" className="eyebrow pt-2">
            {text.examplesLabel}
          </h2>
          <ul className="flex flex-col gap-2.5">
            {text.examples.map((example) => (
              <li key={example.label}>
                <button
                  type="button"
                  className="flex min-h-14 w-full items-center justify-between gap-3 rounded-2xl border-2 border-line bg-card px-4 py-2 text-left text-[1.125rem] font-semibold transition hover:border-line-strong disabled:opacity-60"
                  onClick={() => runExample(example)}
                  disabled={submitting}
                >
                  <span>{example.label}</span>
                  <ArrowRightIcon className="size-5 flex-none text-indigo" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </>
  );
}
