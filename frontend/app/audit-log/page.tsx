"use client";

import { ModeNav } from "@/components/nav/ModeNav";
import { AuditLogView } from "@/components/dashboard/AuditLogView";
import { useAuthStore } from "@/lib/auth/store";

export default function AuditLogPage() {
  const role = useAuthStore((s) => s.role);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-base-border bg-base-bg">
        <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-4 px-4 py-3 sm:px-6">
          <div>
            <h1 className="font-display text-base font-bold tracking-wide text-slate-100 sm:text-lg">
              Audit Log
            </h1>
            <p className="text-[11px] text-slate-500">Administrator-only — every control-affecting action</p>
          </div>
          <ModeNav className="ml-auto" />
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1800px] flex-1 px-4 py-6 sm:px-6">
        {role === "administrator" ? (
          <AuditLogView />
        ) : (
          <p className="text-sm text-slate-500">Administrator role required to view the audit log.</p>
        )}
      </main>
    </div>
  );
}
