import clsx from "clsx";

interface MetricValueProps {
  value: string | number;
  unit?: string;
  size?: "sm" | "md" | "lg" | "xl";
  tone?: "normal" | "amber" | "red" | "cyan";
  className?: string;
}

const sizeClass: Record<NonNullable<MetricValueProps["size"]>, string> = {
  sm: "text-lg",
  md: "text-2xl",
  lg: "text-4xl",
  xl: "text-6xl",
};

const toneClass: Record<NonNullable<MetricValueProps["tone"]>, string> = {
  normal: "text-slate-100",
  amber: "text-status-amber",
  red: "text-status-red",
  cyan: "text-status-cyan",
};

export function MetricValue({ value, unit, size = "md", tone = "normal", className }: MetricValueProps) {
  return (
    <span className={clsx("tabular font-semibold leading-none", sizeClass[size], toneClass[tone], className)}>
      {value}
      {unit && <span className="ml-1 text-[0.5em] font-normal text-slate-500">{unit}</span>}
    </span>
  );
}
