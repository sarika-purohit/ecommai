"use client";

import { useSyncExternalStore } from "react";
import type { Auth } from "firebase/auth";
import { demoModeAllowed, firebaseConfig, isFirebaseConfigured } from "@/lib/config";
import { RETURNING_ATTRIBUTE, RETURNING_KEY } from "@/lib/returning";

export interface AuthUser {
  uid: string;
  name: string | null;
  email: string | null;
}

export type SignInError = "network" | "unauthorizedDomain" | "failed";

export type AuthState =
  | { status: "loading" }
  | { status: "signed-out"; error: SignInError | null; busy: boolean }
  | { status: "signed-in"; user: AuthUser }
  | { status: "demo" }
  | { status: "open" }
  | { status: "misconfigured" };

type AuthModule = typeof import("firebase/auth");

const DEMO_KEY = "ecommai.demo";
const DEVICE_KEY = "ecommai.device";

/** Google sign-in is off unless the app is built with NEXT_PUBLIC_SIGN_IN=on: each browser is then its own shopper. */
export const signInRequired = process.env.NEXT_PUBLIC_SIGN_IN === "on";

const SERVER_STATE: AuthState = { status: "loading" };
const REDIRECT_FALLBACK_CODES = new Set([
  "auth/popup-blocked",
  "auth/operation-not-supported-in-this-environment",
]);
const QUIET_CODES = new Set(["auth/popup-closed-by-user", "auth/cancelled-popup-request"]);

let state: AuthState | null = null;
let firebase: { auth: Auth; module: AuthModule } | null = null;
let firebaseLoading: Promise<{ auth: Auth; module: AuthModule }> | null = null;
let lastError: SignInError | null = null;
const listeners = new Set<() => void>();

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function writeFlag(key: string, on: boolean) {
  try {
    if (on) localStorage.setItem(key, "1");
    else localStorage.removeItem(key);
  } catch {}
}

function initialState(): AuthState {
  if (!signInRequired) return { status: "open" };
  if (isFirebaseConfigured) return { status: "loading" };
  if (!demoModeAllowed) return { status: "misconfigured" };
  return readFlag(DEMO_KEY) ? { status: "demo" } : { status: "signed-out", error: null, busy: false };
}

function emit(next: AuthState) {
  state = next;
  const returning = next.status === "signed-in" || next.status === "demo";
  if (next.status !== "loading") {
    writeFlag(RETURNING_KEY, returning);
    if (!returning) document.documentElement.removeAttribute(RETURNING_ATTRIBUTE);
  }
  listeners.forEach((listener) => listener());
}

function toUser(user: { uid: string; displayName: string | null; email: string | null }): AuthUser {
  return { uid: user.uid, name: user.displayName, email: user.email };
}

function toSignInError(error: unknown): SignInError {
  const code = (error as { code?: string } | null)?.code ?? "";
  if (code === "auth/network-request-failed") return "network";
  if (code === "auth/unauthorized-domain") return "unauthorizedDomain";
  return "failed";
}

function showError(error: SignInError | null) {
  lastError = error;
  if (state?.status === "signed-out") emit({ status: "signed-out", error, busy: false });
}

function loadFirebase(): Promise<{ auth: Auth; module: AuthModule }> {
  if (firebaseLoading) return firebaseLoading;
  firebaseLoading = (async () => {
    const [{ getApps, initializeApp }, module] = await Promise.all([import("firebase/app"), import("firebase/auth")]);
    const app = getApps()[0] ?? initializeApp(firebaseConfig);
    const auth = module.getAuth(app);
    auth.languageCode = navigator.language;
    firebase = { auth, module };
    module.onAuthStateChanged(auth, (user) => {
      emit(user ? { status: "signed-in", user: toUser(user) } : { status: "signed-out", error: lastError, busy: false });
    });
    module.getRedirectResult(auth).catch((error: unknown) => showError(toSignInError(error)));
    return firebase;
  })();
  return firebaseLoading;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (signInRequired && isFirebaseConfigured) void loadFirebase();
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): AuthState {
  if (!state) state = initialState();
  return state;
}

function getServerSnapshot(): AuthState {
  return signInRequired ? SERVER_STATE : { status: "open" };
}

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export async function signInWithGoogle(): Promise<void> {
  const { auth, module } = firebase ?? (await loadFirebase());
  const provider = new module.GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  if (state?.status === "signed-out") emit({ status: "signed-out", error: null, busy: true });
  lastError = null;
  try {
    await module.signInWithPopup(auth, provider);
  } catch (error) {
    const code = (error as { code?: string } | null)?.code ?? "";
    if (REDIRECT_FALLBACK_CODES.has(code)) {
      await module.signInWithRedirect(auth, provider);
      return;
    }
    showError(QUIET_CODES.has(code) ? null : toSignInError(error));
  }
}

export function enterDemoMode() {
  if (!demoModeAllowed) return;
  writeFlag(DEMO_KEY, true);
  emit({ status: "demo" });
}

export async function signOut(): Promise<void> {
  if (state?.status === "demo") {
    writeFlag(DEMO_KEY, false);
    emit({ status: "signed-out", error: null, busy: false });
    return;
  }
  if (firebase) await firebase.module.signOut(firebase.auth);
}

/** The Firebase ID token for API calls, or null in demo mode. The SDK refreshes it when it's close to expiry. */
export async function getIdToken(): Promise<string | null> {
  if (!signInRequired || !isFirebaseConfigured) return null;
  const { auth } = firebase ?? (await loadFirebase());
  await auth.authStateReady();
  return auth.currentUser ? auth.currentUser.getIdToken() : null;
}

/** This browser's random shopper id, made on first use, sent as X-Device-Id when sign-in is off. */
export function deviceId(): string {
  try {
    const saved = localStorage.getItem(DEVICE_KEY);
    if (saved) return saved;
    const made = crypto.randomUUID();
    localStorage.setItem(DEVICE_KEY, made);
    return made;
  } catch {
    return crypto.randomUUID();
  }
}

/** Forgets this browser's shopper id, so the next request starts a fresh shopper. */
export function forgetDevice() {
  try {
    localStorage.removeItem(DEVICE_KEY);
  } catch {}
}
