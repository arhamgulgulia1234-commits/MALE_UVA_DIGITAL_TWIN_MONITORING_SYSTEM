"use client";

import type { ReactNode } from "react";
import { useAuthStore } from "@/lib/auth/store";
import { LoginScreen } from "./LoginScreen";

/** Single choke point for the whole app: no session, no dashboard. Wraps
 * app/layout.tsx's children rather than gating each page individually. */
export function AuthGate({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token);
  if (!token) return <LoginScreen />;
  return <>{children}</>;
}
