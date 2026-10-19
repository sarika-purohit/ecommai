"use client";

import type { CSSProperties } from "react";
import { CheckIcon, GoogleLogo, TagMark } from "@/components/icons";
import { Tag } from "@/components/Tag";
import { enterDemoMode, signInWithGoogle, type AuthState } from "@/lib/auth";
import { demoModeAllowed } from "@/lib/config";
import { strings } from "@/lib/strings";

const text = strings.signIn;

export function SignInScreen({ auth }: { auth: AuthState }) {
  const busy = auth.status === "signed-out" && auth.busy;
  const error = auth.status === "signed-out" ? auth.error : null;

  return (
    <main data-signin-screen className="shell flex min-h-dvh flex-col pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-6">
      <p className="display flex items-center gap-2 text-[1.375rem] text-indigo">
        <TagMark className="h-5 w-8" />
        {strings.appName}
      </p>

      <section className="mt-10 flex-1" aria-labelledby="signin-title">
        <h1 id="signin-title" className="display text-[2.75rem] sm:text-[3.25rem]">
          {text.title}
        </h1>
        <p className="mt-4 max-w-[30ch] text-[1.25rem] leading-snug text-ink-soft">{text.lede}</p>

        <ul className="mt-8 flex flex-wrap gap-2.5" aria-hidden="true">
          {text.heroTags.map((label, order) => (
            <li key={label}>
              <Tag>
                <span className="tag-text gap-2">
                  {label}
                  <span
                    className="stamp inline-flex size-6 items-center justify-center rounded-full bg-pass text-white"
                    style={{ "--order": order } as CSSProperties}
                  >
                    <CheckIcon className="size-4" strokeWidth={3} />
                  </span>
                </span>
              </Tag>
            </li>
          ))}
        </ul>
        <p className="mt-3 flex items-center gap-2 text-base text-pass" aria-hidden="true">
          <span className="inline-block h-0.5 w-6 bg-pass" />
          {text.heroProof}
        </p>
      </section>

      <div className="mt-10 flex flex-col gap-3">
        {auth.status === "misconfigured" ? (
          <div role="alert" className="panel border-l-4 border-fail p-5">
            <h2 className="display text-[1.5rem] text-fail">{text.misconfiguredTitle}</h2>
            <p className="mt-2 text-ink-soft">{text.misconfiguredBody}</p>
          </div>
        ) : (
          <>
            <button
              type="button"
              className="btn btn-secondary w-full"
              onClick={() => void signInWithGoogle()}
              disabled={busy}
            >
              <GoogleLogo className="size-6" />
              {busy ? text.googleBusy : text.google}
            </button>
            {error ? (
              <p role="alert" className="field-error">
                {text.errors[error]}
              </p>
            ) : null}
            {demoModeAllowed ? (
              <div className="mt-2 rounded-2xl border-2 border-dashed border-line-strong p-4">
                <p className="eyebrow">{text.demoBadge}</p>
                <button type="button" className="btn btn-primary mt-2 w-full" onClick={enterDemoMode}>
                  {text.demo}
                </button>
                <p className="mt-2 text-base text-ink-soft">{text.demoNote}</p>
              </div>
            ) : null}
          </>
        )}
        <p className="mt-1 text-base text-ink-soft">{text.privacy}</p>
      </div>
    </main>
  );
}
