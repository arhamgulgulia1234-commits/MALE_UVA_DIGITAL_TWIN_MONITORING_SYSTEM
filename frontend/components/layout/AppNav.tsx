"use client";

/**
 * The one persistent nav: mode tabs, signed-in user + role, logout, idle-timeout
 * warning. Lives in app/(app)/layout.tsx so it mounts once and never re-renders across
 * client-side navigation between the pages it links to — that persistence is what stops
 * the nav visibly shifting/re-mounting per page (see docs on the (app) route group).
 */
import Link from "next/link";
import clsx from "clsx";
import { usePathname } from "next/navigation";
import { useAuthStore, type Role } from "@/lib/auth/store";
import { useIdleLogout } from "@/lib/auth/useIdleLogout";

const MODES = [
  { href: "/", label: "Live Dashboard", hint: "Streaming telemetry" },
  { href: "/test-bench", label: "Test Bench", hint: "Offline what-if" },
  { href: "/fleet", label: "Fleet", hint: "Squadron-wide health" },
] as const;

const AUDIT_MODE = { href: "/audit-log", label: "Audit Log", hint: "Every control-affecting action" } as const;

const ROLE_LABEL: Record<Role, string> = {
  operator: "Operator",
  maintenance_engineer: "Maintenance Engineer",
  administrator: "Administrator",
};

export function AppNav() {
  const pathname = usePathname();
  const role = useAuthStore((s) => s.role);
  const username = useAuthStore((s) => s.username);
  const logout = useAuthStore((s) => s.logout);
  const { secondsLeft, stayActive } = useIdleLogout();
  const modes = role === "administrator" ? [...MODES, AUDIT_MODE] : MODES;

  return (
    <>
      <nav
        className="page-container sticky top-0 z-40 flex h-12 items-center gap-1 border-b border-base-border bg-base-bg"
        aria-label="Application mode"
      >
        {modes.map((mode) => {
          const active = pathname === mode.href;
          return (
            <Link
              key={mode.href}
              href={mode.href}
              aria-current={active ? "page" : undefined}
              title={mode.hint}
              className={clsx(
                "rounded-md px-3 py-1.5 font-display text-xs font-semibold uppercase tracking-[0.07em] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-status-cyan",
                active
                  ? "bg-status-cyan/15 text-status-cyan shadow-glow"
                  : "text-slate-400 hover:text-slate-300"
              )}
            >
              {mode.label}
            </Link>
          );
        })}
        {username && role && (
          <button
            type="button"
            onClick={logout}
            title="Click to sign out"
            className="ml-auto flex items-center gap-1.5 rounded-md px-2 py-1.5 font-mono text-[10px] text-slate-400 transition-colors hover:text-status-red focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-status-cyan"
          >
            <span className="text-slate-400">{username}</span>
            <span aria-hidden="true">·</span>
            <span>{ROLE_LABEL[role]}</span>
            <span aria-hidden="true">⏻</span>
            <span className="sr-only">Sign out</span>
          </button>
        )}
      </nav>

      {secondsLeft !== null && (
        <div
          role="alertdialog"
          aria-label="Session expiring"
          className="fixed inset-x-0 top-14 z-50 mx-auto w-fit rounded-lg border border-status-amber/50 bg-base-panel px-4 py-2.5 text-[12px] shadow-glow-amber"
        >
          <span className="text-slate-200">
            Signed out in <span className="tabular font-semibold text-status-amber">{secondsLeft}s</span> due to inactivity.
          </span>
          <button
            type="button"
            onClick={stayActive}
            className="ml-3 rounded-md border border-status-cyan/50 bg-status-cyan/10 px-2.5 py-1 font-mono text-[10px] uppercase text-status-cyan transition-colors hover:bg-status-cyan/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-status-cyan"
          >
            Stay signed in
          </button>
        </div>
      )}
    </>
  );
}
