"use client";

import clsx from "clsx";
import { ENGINE_PARTS } from "@/lib/enginePartsRegistry";

/**
 * "Module Map" view: the same registry the 3D scene is driven by, rendered as a flat
 * list of physical part → backend module.
 *
 * The 3D view answers "where is this thing"; this answers "what code owns it", for the
 * whole engine at once instead of one part at a time. Picking a row selects that part on
 * the model and drops back into the 3D view with the detail panel already open, so the
 * two views are two readings of one selection rather than two separate features.
 */
export function ModuleMap({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="absolute inset-0 z-10 overflow-y-auto bg-base-bg px-3 pb-3 pt-12">
      <div className="mb-2 font-mono text-[8px] uppercase tracking-[0.1em] text-slate-400">
        Physical part → simulating module
      </div>
      <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
        {ENGINE_PARTS.map((part) => (
          <button
            key={part.id}
            onClick={() => onSelect(part.id)}
            className={clsx(
              "group rounded-md border px-2 py-1.5 text-left transition-colors",
              selectedId === part.id
                ? "border-status-cyan/50 bg-status-cyan/10"
                : "border-base-border bg-base-panel/70 hover:border-status-cyan/30 hover:bg-base-panel2/80"
            )}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate text-[10px] font-medium text-slate-200">
                {part.displayName}
              </span>
              <span className="shrink-0 font-mono text-[7px] uppercase tracking-[0.08em] text-slate-400">
                {part.healthSubsystem}
              </span>
            </div>
            <code className="mt-0.5 block truncate font-mono text-[8px] text-status-cyan/85">
              {part.backendModule.replace("backend/app/", "")}
            </code>
          </button>
        ))}
      </div>
    </div>
  );
}
