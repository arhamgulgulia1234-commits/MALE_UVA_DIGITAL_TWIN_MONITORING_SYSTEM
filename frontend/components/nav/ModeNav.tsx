"use client";

/**
 * Switches between the Live Dashboard and the Test Bench.
 *
 * Kept as its own component, mounted with a single line on each page, so adding Phase 4
 * navigation did not mean restructuring MissionHeader or the dashboard layout.
 */
import Link from "next/link";
import clsx from "clsx";
import { usePathname } from "next/navigation";

const MODES = [
  { href: "/", label: "Live Dashboard", hint: "Streaming telemetry" },
  { href: "/test-bench", label: "Test Bench", hint: "Offline what-if" },
  // Phase 6: the one nav tab that spans the whole fleet rather than one UAV — every
  // other tab here is scoped to whichever UAV MissionHeader's selector currently has
  // picked, which is exactly why this one is not folded into the Live Dashboard the
  // way Lifecycle and the performance map were.
  { href: "/fleet", label: "Fleet", hint: "Squadron-wide health" },
] as const;

export function ModeNav({ className }: { className?: string }) {
  const pathname = usePathname();

  return (
    <nav
      className={clsx(
        "flex items-center gap-1 rounded-lg border border-base-border bg-base-panel/70 p-1 backdrop-blur",
        className
      )}
      aria-label="Application mode"
    >
      {MODES.map((mode) => {
        const active = pathname === mode.href;
        return (
          <Link
            key={mode.href}
            href={mode.href}
            aria-current={active ? "page" : undefined}
            title={mode.hint}
            className={clsx(
              "rounded-md px-3 py-1.5 font-display text-xs font-semibold uppercase tracking-[0.12em] transition-colors",
              active
                ? "bg-status-cyan/15 text-status-cyan shadow-glow"
                : "text-slate-500 hover:text-slate-300"
            )}
          >
            {mode.label}
          </Link>
        );
      })}
    </nav>
  );
}
