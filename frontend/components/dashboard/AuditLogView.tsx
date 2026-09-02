"use client";

import { useEffect, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { API_URL } from "@/lib/testbench/api";
import { getAuthToken } from "@/lib/auth/store";
import { formatTimeHHMMSS } from "@/lib/format";

interface AuditEntry {
  id: number;
  timestamp: string;
  username: string;
  role: string;
  action: string;
  parameters: Record<string, unknown>;
}

const PAGE_SIZE = 25;

export function AuditLogView() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const token = getAuthToken();
    fetch(`${API_URL}/audit-log?limit=${PAGE_SIZE}&offset=${offset}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => {
        if (!res.ok) throw new Error(res.status === 403 ? "Administrator role required" : `HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        setEntries(data.entries);
        setTotal(data.total);
        setError(null);
      })
      .catch((err) => !cancelled && setError(String(err.message ?? err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [offset]);

  return (
    <GlassCard
      title="Audit Log"
      subtitle="Every control-affecting action — fault inject/clear, presets, maintenance, missions, logins"
      glow="cyan"
      bodyClassName="p-0"
    >
      {error && (
        <p className="p-4 text-[11px] text-status-red">{error}</p>
      )}
      {!error && (
        <div className="max-h-[600px] overflow-y-auto">
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 bg-base-panel">
              <tr className="border-b border-base-border/70 text-[10px] uppercase tracking-wide text-slate-500">
                <th className="px-3 py-2 text-left font-medium">Time</th>
                <th className="px-3 py-2 text-left font-medium">User</th>
                <th className="px-3 py-2 text-left font-medium">Role</th>
                <th className="px-3 py-2 text-left font-medium">Action</th>
                <th className="px-3 py-2 text-left font-medium">Parameters</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className="border-b border-base-border/40">
                  <td className="tabular px-3 py-1.5 text-slate-400">
                    {formatTimeHHMMSS(new Date(e.timestamp).getTime() / 1000)}
                  </td>
                  <td className="px-3 py-1.5 text-slate-200">{e.username}</td>
                  <td className="px-3 py-1.5 text-slate-500">{e.role}</td>
                  <td className="px-3 py-1.5 font-medium text-status-cyan">{e.action}</td>
                  <td className="tabular px-3 py-1.5 text-slate-500">
                    {Object.keys(e.parameters).length > 0 ? JSON.stringify(e.parameters) : "—"}
                  </td>
                </tr>
              ))}
              {!loading && entries.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-slate-500">
                    No actions recorded yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex items-center justify-between border-t border-base-border/70 px-3 py-2 text-[11px] text-slate-500">
        <span>
          {total > 0 ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}` : "—"}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            className="rounded-md border border-base-border px-2 py-1 uppercase tracking-wide text-slate-400 transition-colors hover:border-status-cyan/40 disabled:opacity-30"
          >
            Prev
          </button>
          <button
            type="button"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            className="rounded-md border border-base-border px-2 py-1 uppercase tracking-wide text-slate-400 transition-colors hover:border-status-cyan/40 disabled:opacity-30"
          >
            Next
          </button>
        </div>
      </div>
    </GlassCard>
  );
}
