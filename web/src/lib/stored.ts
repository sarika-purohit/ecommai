"use client";

import { useSyncExternalStore } from "react";

type Area = "local" | "session";

function subscribeNever() {
  return () => {};
}

/** False while prerendering and hydrating, true once the browser has taken over. */
export function useIsClient(): boolean {
  return useSyncExternalStore(
    subscribeNever,
    () => true,
    () => false,
  );
}

export interface StoredValue<T> {
  get(): T | null;
  set(value: T | null): void;
  use(): T | null;
}

function area(kind: Area): Storage | null {
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

/** A JSON value kept in browser storage, readable from components without hydration mismatches. */
export function storedValue<T>(key: string, kind: Area): StoredValue<T> {
  const listeners = new Set<() => void>();
  let cachedRaw: string | null = null;
  let cachedValue: T | null = null;

  function get(): T | null {
    const raw = area(kind)?.getItem(key) ?? null;
    if (raw !== cachedRaw) {
      cachedRaw = raw;
      try {
        cachedValue = raw === null ? null : (JSON.parse(raw) as T);
      } catch {
        cachedValue = null;
      }
    }
    return cachedValue;
  }

  function set(value: T | null) {
    const storage = area(kind);
    try {
      if (value === null) storage?.removeItem(key);
      else storage?.setItem(key, JSON.stringify(value));
    } catch {}
    listeners.forEach((listener) => listener());
  }

  function subscribe(listener: () => void) {
    listeners.add(listener);
    const onStorage = (event: StorageEvent) => {
      if (event.key === key) listener();
    };
    window.addEventListener("storage", onStorage);
    return () => {
      listeners.delete(listener);
      window.removeEventListener("storage", onStorage);
    };
  }

  function getServerSnapshot(): T | null {
    return null;
  }

  function use(): T | null {
    return useSyncExternalStore(subscribe, get, getServerSnapshot);
  }

  return { get, set, use };
}
