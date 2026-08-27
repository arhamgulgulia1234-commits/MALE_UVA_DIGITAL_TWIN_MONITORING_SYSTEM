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
  pulse?: boolean;
  className?: string;
}

export function StatusPill({ tone, children, pulse, className }: StatusPillProps) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] font-medium uppercase tracking-wider",
        toneClass[tone],
        pulse && "animate-pulseGlow",
        className
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", {
        "bg-status-go": tone === "go",
        "bg-status-amber": tone === "caution",
        "bg-status-red": tone === "nogo",
        "bg-status-idle": tone === "idle",
        "bg-status-cyan": tone === "cyan",
      })} />
      {children}
    </span>
  );
}
