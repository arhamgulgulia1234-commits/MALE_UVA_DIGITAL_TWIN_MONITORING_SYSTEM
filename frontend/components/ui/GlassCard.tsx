import clsx from "clsx";
import type { ReactNode } from "react";

type Glow = "none" | "cyan" | "amber" | "red" | "go";

const glowClass: Record<Glow, string> = {
  none: "shadow-none",
  cyan: "shadow-glow",
  amber: "shadow-glow-amber",
  red: "shadow-glow-red",
  go: "shadow-glow-go",
};

interface GlassCardProps {
  title?: string;
  subtitle?: string;
  glow?: Glow;
  className?: string;
  bodyClassName?: string;
  headerRight?: ReactNode;
  children: ReactNode;
}

export function GlassCard({
  title,
  subtitle,
  glow = "cyan",
  className,
  bodyClassName,
  headerRight,
  children,
}: GlassCardProps) {
  return (
    <div className={clsx("glass-panel", glowClass[glow], "flex flex-col", className)}>
      {(title || headerRight) && (
        <div className="flex items-center justify-between gap-3 border-b border-base-border/70 px-4 py-3">
          <div>
            {title && <h2 className="panel-title">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[11px] text-slate-400">{subtitle}</p>}
          </div>
          {headerRight}
        </div>
      )}
      <div className={clsx("flex-1", bodyClassName ?? "p-4")}>{children}</div>
    </div>
  );
}
