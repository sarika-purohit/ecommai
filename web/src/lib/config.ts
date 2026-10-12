export const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY ?? "",
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN ?? "",
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID ?? "",
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID ?? "",
};

export const isFirebaseConfigured = Object.values(firebaseConfig).every((value) => value.trim() !== "");

export const isProduction = process.env.NODE_ENV === "production";

export const demoModeAllowed = !isFirebaseConfigured && !isProduction;

export const apiBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").trim().replace(/\/+$/, "");

export const mockApi = process.env.NEXT_PUBLIC_USE_MOCK === "1" || apiBaseUrl === "";
