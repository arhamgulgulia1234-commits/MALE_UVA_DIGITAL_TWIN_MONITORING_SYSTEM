"use client";

import { useState } from "react";
import { API_URL } from "@/lib/testbench/api";
import { useAuthStore, type Role } from "@/lib/auth/store";
import { PRODUCT_NAME } from "@/lib/branding";

interface LoginResponse {
  access_token: string;
  username: string;
  role: Role;
  demo_mode: boolean;
}

export function LoginScreen() {
  const setSession = useAuthStore((s) => s.setSession);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        setError(res.status === 401 ? "Invalid username or password" : `Login failed (${res.status})`);
        return;
      }
      const data: LoginResponse = await res.json();
      setSession({
        token: data.access_token,
        username: data.username,
        role: data.role,
        demoMode: data.demo_mode,
      });
    } catch {
      setError(`No response from ${API_URL}. Is the backend running?`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-base-bg px-4">
      <form
        onSubmit={onSubmit}
        className="glass-panel w-full max-w-sm space-y-4 p-6"
      >
        <div className="text-center">
          <h1 className="font-display text-lg font-bold tracking-wide text-slate-100">
            {PRODUCT_NAME}
          </h1>
          <p className="mt-1 text-[11px] text-slate-400">Sign in to continue</p>
        </div>

        <label className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-400">
            Username
          </span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
            className="w-full rounded-md border border-base-border bg-base-panel2 px-3 py-2 text-sm text-slate-100 outline-none focus:border-status-cyan/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-cyan"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-400">
            Password
          </span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            className="w-full rounded-md border border-base-border bg-base-panel2 px-3 py-2 text-sm text-slate-100 outline-none focus:border-status-cyan/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-cyan"
          />
        </label>

        {error && (
          <p role="alert" className="rounded-md border border-status-red/40 bg-status-red/10 px-3 py-2 text-[11px] text-status-red">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md border border-status-cyan/50 bg-status-cyan/15 px-4 py-2 font-display text-sm font-semibold uppercase tracking-wide text-status-cyan transition-colors hover:bg-status-cyan/25 disabled:opacity-50"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
