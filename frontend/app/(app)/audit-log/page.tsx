"use client";

import { AuditLogView } from "@/components/dashboard/AuditLogView";
import { useAuthStore } from "@/lib/auth/store";

export default function AuditLogPage() {
  const role = useAuthStore((s) => s.role);

  return (
    <>
      <header className="border-b border-base-border bg-base-bg">
        <div className="page-container flex flex-wrap items-center gap-4 py-3">
          <div>
            <h1 className="font-display text-base font-bold tracking-wide text-slate-100 sm:text-lg">
              Audit Log
            </h1>
            <p className="text-[11px] text-slate-400">Administrator-only — every control-affecting action</p>
          </div>
        </div>
      </header>

      <main className="page-container flex-1 py-6">
        {role === "administrator" ? (
          <AuditLogView />
        ) : (
          <p className="text-sm text-slate-400">Administrator role required to view the audit log.</p>
        )}
      </main>
    </>
  );
}
