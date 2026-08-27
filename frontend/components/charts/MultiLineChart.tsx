"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface SeriesDef {
  key: string;
  color: string;
  label: string;
  yAxisId?: "left" | "right";
}

interface MultiLineChartProps {
  data: Record<string, number>[];
  series: SeriesDef[];
  height?: number;
  xKey?: string;
}

function ChartTooltip({ active, payload, series }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-base-border bg-base-panel2/95 px-3 py-2 text-[11px] shadow-glow backdrop-blur">
      {payload.map((p: any) => {
        const def = series.find((s: SeriesDef) => s.key === p.dataKey);
        return (
          <div key={p.dataKey} className="flex items-center gap-2 tabular">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: p.color }} />
            <span className="text-slate-400">{def?.label ?? p.dataKey}</span>
            <span className="ml-auto font-medium text-slate-100">{Number(p.value).toFixed(1)}</span>
          </div>
        );
      })}
    </div>
  );
}

export function MultiLineChart({ data, series, height = 220, xKey = "t" }: MultiLineChartProps) {
  const hasRight = series.some((s) => s.yAxisId === "right");
  return (
    <div style={{ height, width: "100%" }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: hasRight ? 8 : 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#1e2734" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={{ fill: "#5b6b82", fontSize: 10, fontFamily: "var(--font-mono)" }}
            tickFormatter={(v) => `${v}s`}
            axisLine={{ stroke: "#1e2734" }}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            yAxisId="left"
            tick={{ fill: "#5b6b82", fontSize: 10, fontFamily: "var(--font-mono)" }}
            axisLine={false}
            tickLine={false}
            width={40}
          />
          {hasRight && (
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fill: "#5b6b82", fontSize: 10, fontFamily: "var(--font-mono)" }}
              axisLine={false}
              tickLine={false}
              width={40}
            />
          )}
          <Tooltip content={<ChartTooltip series={series} />} />
          {series.map((s) => (
            <Line
              key={s.key}
              yAxisId={s.yAxisId ?? "left"}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={1.75}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
