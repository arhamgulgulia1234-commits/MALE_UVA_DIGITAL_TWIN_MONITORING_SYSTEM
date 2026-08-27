"use client";

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

interface BarSpectrumProps {
  data: { label: string; value: number; tone: "normal" | "warn" | "critical" }[];
  height?: number;
  maxValue?: number;
}

const toneColor: Record<string, string> = {
  normal: "#3fd0e0",
  warn: "#f5a623",
  critical: "#ef4a5f",
};

export function BarSpectrum({ data, height = 180, maxValue }: BarSpectrumProps) {
  return (
    <div style={{ height, width: "100%" }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <XAxis
            dataKey="label"
            tick={{ fill: "#5b6b82", fontSize: 10, fontFamily: "var(--font-mono)" }}
            axisLine={{ stroke: "#1e2734" }}
            tickLine={false}
          />
          <YAxis
            domain={[0, maxValue ?? "dataMax + 0.1"]}
            tick={{ fill: "#5b6b82", fontSize: 10, fontFamily: "var(--font-mono)" }}
            axisLine={false}
            tickLine={false}
            width={36}
          />
          <Tooltip
            cursor={{ fill: "rgba(63,208,224,0.06)" }}
            contentStyle={{
              background: "#131a28",
              border: "1px solid #1e2734",
              borderRadius: 8,
              fontSize: 11,
              fontFamily: "var(--font-mono)",
            }}
          />
          <Bar dataKey="value" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell key={i} fill={toneColor[d.tone]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
