"use client";

import type { ReactNode } from "react";
import { SignInScreen } from "@/components/auth/SignInScreen";
import { useAuth } from "@/lib/auth";

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();
  if (auth.status === "signed-in" || auth.status === "demo") return <>{children}</>;
  return <SignInScreen auth={auth} />;
}
