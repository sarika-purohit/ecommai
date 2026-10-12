"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { ArrowLeftIcon, PersonIcon, TagMark } from "@/components/icons";
import { signOut, useAuth } from "@/lib/auth";
import { sessionStore } from "@/lib/session";
import { strings } from "@/lib/strings";

interface AppHeaderProps {
  back?: { href: string; label: string };
}

export function AppHeader({ back }: AppHeaderProps) {
  return (
    <header className="shell flex h-16 items-center justify-between gap-3">
      {back ? (
        <Link href={back.href} className="link -ml-1 no-underline hover:underline">
          <ArrowLeftIcon className="size-5" />
          {back.label}
        </Link>
      ) : (
        <p className="display flex items-center gap-2 text-[1.375rem] text-indigo">
          <TagMark className="h-5 w-8" />
          {strings.appName}
        </p>
      )}
      <AccountMenu />
    </header>
  );
}

function AccountMenu() {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const wrapper = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(event: PointerEvent) {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const user = auth.status === "signed-in" ? auth.user : null;
  const initial = (user?.name ?? user?.email ?? "").trim().charAt(0).toUpperCase();

  async function handleSignOut() {
    setOpen(false);
    sessionStore.set(null);
    await signOut();
  }

  return (
    <div ref={wrapper} className="relative">
      <button
        ref={button}
        type="button"
        className="flex size-11 items-center justify-center rounded-full bg-wash font-display text-lg font-bold text-indigo"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={strings.account.open}
        onClick={() => setOpen((value) => !value)}
      >
        {initial || <PersonIcon className="size-5" />}
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="panel absolute right-0 top-[calc(100%+0.5rem)] z-20 w-[min(18rem,calc(100vw-2.5rem))] p-4"
      >
        {user ? (
          <>
            <p className="text-base text-ink-soft">{strings.account.signedInAs}</p>
            <p className="truncate font-semibold">{user.name ?? user.email}</p>
            {user.name && user.email ? <p className="truncate text-base text-ink-soft">{user.email}</p> : null}
          </>
        ) : (
          <p className="font-semibold">{strings.account.demoUser}</p>
        )}
        <button type="button" className="btn btn-secondary btn-small mt-3 w-full" onClick={() => void handleSignOut()}>
          {strings.account.signOut}
        </button>
      </div>
    </div>
  );
}
