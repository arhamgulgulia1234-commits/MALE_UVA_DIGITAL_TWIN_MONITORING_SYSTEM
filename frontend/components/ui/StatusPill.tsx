import clsx from "clsx";

type Tone = "go" | "caution" | "nogo" | "idle" | "cyan";

const toneClass: Record<Tone, string> = {
  go: "bg-status-go/15 text-status-go border-status-go/40",
  caution: "bg-status-amber/15 text-status-amber border-status-amber/40",
  nogo: "bg-status-red/15 text-status-red border-status-red/40",
  idle: "bg-status-idle/10 text-status-idle border-status-idle/30",
  cyan: "bg-status-cyan/15 text-status-cyan border-status-cyan/40",
};

interface StatusPillProps {
  tone: Tone;
  children: React.ReactNode;
  /** No longer animated (control-room restyle dropped decorative pulsing on idle
   * badges) — kept as a prop so callers marking a live/urgent state don't need
   * updating; it currently has no visual effect. */
  pulse?: boolean;
  className?: string;
}

export function StatusPill({ tone, children, className }: StatusPillProps) {
  return (
    <span
      role="status"
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] font-medium uppercase tracking-wider",
        toneClass[tone],
        className
      )}
    >
      <span
        aria-hidden="true"
        className={clsx("h-1.5 w-1.5 rounded-full", {
          "bg-status-go": tone === "go",
          "bg-status-amber": tone === "caution",
          "bg-status-red": tone === "nogo",
          "bg-status-idle": tone === "idle",
          "bg-status-cyan": tone === "cyan",
        })}
      />
      {children}
    </span>
  );
}
