/**
 * In-memory session state — the JWT never touches localStorage/sessionStorage, so a
 * page reload requires logging in again. This is the one shared store every fetch/WS
 * choke point (lib/store.ts, lib/testbench/api.ts, hooks/useTelemetryStream.ts) reads
 * the token from.
 */
import { create } from "zustand";

export type Role = "operator" | "maintenance_engineer" | "administrator";

interface AuthState {
  token: string | null;
  username: string | null;
  role: Role | null;
  demoMode: boolean;
  setSession: (session: { token: string; username: string; role: Role; demoMode: boolean }) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  username: null,
  role: null,
  demoMode: true,
  setSession: ({ token, username, role, demoMode }) => set({ token, username, role, demoMode }),
  logout: () => set({ token: null, username: null, role: null }),
}));

/** Non-hook accessor for plain functions (fetch wrappers, WS URL builders). */
export function getAuthToken(): string | null {
  return useAuthStore.getState().token;
}
