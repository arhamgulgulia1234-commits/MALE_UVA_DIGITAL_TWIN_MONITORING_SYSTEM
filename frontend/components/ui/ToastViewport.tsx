"use client";

import { useEffect } from "react";
import clsx from "clsx";
import { useToastStore } from "@/lib/toast/store";

const AUTO_DISMISS_MS = 6000;

function ToastItem({ id, tone, message }: { id: number; tone: "error" | "info"; message: string }) {
  const dismiss = useToastStore((s) => s.dismiss);
  useEffect(() => {
    const t = setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    return () => clearTimeout(t);
  }, [id, dismiss]);

  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={clsx(
        "flex items-start gap-2 rounded-lg border px-3 py-2 text-[12px] shadow-glow",
        tone === "error"
          ? "border-status-red/50 bg-base-panel text-status-red"
          : "border-status-cyan/50 bg-base-panel text-status-cyan"
      )}
    >
      <span aria-hidden="true">{tone === "error" ? "⚠" : "ℹ"}</span>
      <span className="flex-1 text-slate-200">{message}</span>
      <button
        type="button"
        onClick={() => dismiss(id)}
        aria-label="Dismiss"
        className="text-slate-400 hover:text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-status-cyan"
      >
        ×
      </button>
    </div>
  );
}

/** Fixed bottom-right stack, one live region for all toasts pushed via lib/toast/store.ts. */
export function ToastViewport() {
  const toasts = useToastStore((s) => s.toasts);
  if (toasts.length === 0) return null;
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
    >
      {toasts.map((t) => (
        <div key={t.id} className="pointer-events-auto">
          <ToastItem {...t} />
        </div>
      ))}
    </div>
  );
}
