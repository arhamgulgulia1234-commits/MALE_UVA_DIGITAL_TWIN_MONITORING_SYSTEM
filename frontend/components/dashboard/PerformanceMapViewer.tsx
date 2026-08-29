"use client";

/**
 * Engine performance map — the steady-state RPM x load surface, with the live engine and
 * the optimizer's recommendation plotted on top of it.
 *
 * The point of this panel is the *third* layer. A performance map on its own is a
 * reference chart: it says what the engine can do, at every speed and load, at one
 * altitude. Useful, and static. What makes it operational is putting two dots on it — one
 * for where the engine actually is right now, one for where the Phase 4 optimizer says it
 * should be — because the distance between them is the recommendation, drawn against the
 * surface that explains *why*. "Move right and down" is an instruction; the same arrow
 * across a BSFC island is an argument.
 *
 * Three things worth knowing about how it is wired:
 *
 * **The surface is not telemetry.** It comes from `generate_performance_map()`, which
 * evaluates the steady-state engine equations on a dyno grid — crank speed held, every lag
 * settled, no thermal transient. It is a property of the engine's parameters at one
 * altitude, so it is fetched once per altitude and cached on both sides.
 *
 * **The live marker is polled, not streamed**, even though this panel lives on the page
 * that already has a WebSocket. `TelemetryFrame` (lib/types.ts) carries RPM but never the
 * *commanded* throttle — that lever only exists on `SimulationLoop` — so the marker's
 * other axis has nowhere to come from on the wire. Rather than widen the live telemetry
 * contract for one marker, it is a 1 Hz GET against `/performance-maps/live-point`, paused
 * when the tab is hidden.
 *
 * **Altitude is stated, never assumed.** The map's altitude comes from the slider; the
 * live engine has its own, and the optimizer's result carries a third. RPM and throttle are
 * the axes, so a marker's *position* is right regardless — but the surface underneath it
 * is only that engine's surface when the altitudes agree. When they do not, the panel says
 * so and offers to move the map, rather than quietly drawing a point on the wrong sheet.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { GlassCard } from "@/components/ui/GlassCard";
import { StatusPill } from "@/components/ui/StatusPill";
import {
  TestBenchError,
  fetchLiveOperatingPoint,
  fetchPerformanceMap,
} from "@/lib/testbench/api";
import { useTestBenchStore } from "@/lib/testbench/store";
import type {
  LiveOperatingPoint,
  PerformanceMapResponse,
  PerformanceMetric,
} from "@/lib/testbench/types";
import {
  colorDomain,
  contourLevels,
  drawMap,
  formatMetric,
  legendTicks,
  normalise,
  rampColor,
  sampleNearest,
  type ColorDomain,
} from "./performanceMapRender";

const METRICS: { name: PerformanceMetric; label: string; short: string }[] = [
  { name: "power", label: "Power", short: "kW" },
  { name: "bsfc", label: "BSFC", short: "g/kWh" },
  { name: "volumetric_efficiency", label: "Volumetric Eff.", short: "%" },
];

const ALTITUDE_MIN = 0;
const ALTITUDE_MAX = 8000;
const ALTITUDE_STEP = 100;

/** How far the live (or recommended) altitude may differ before the panel says so. */
const ALTITUDE_MATCH_TOLERANCE_M = 300;

const LIVE_POLL_MS = 1000;

/** Slider debounce. Long enough to skip the frames of a drag, short enough to feel live. */
const ALTITUDE_DEBOUNCE_MS = 220;

const PLOT_HEIGHT = 330;
const MARGIN = { top: 10, right: 14, bottom: 34, left: 54 };

const LIVE_COLOR = "#f5a623";
const TARGET_COLOR = "#e8f3ff";

type MapsByMetric = Partial<Record<PerformanceMetric, PerformanceMapResponse>>;

interface MarkerGeometry {
  x: number;
  y: number;
  offMap: boolean;
}

function project(
  rpm: number,
  throttlePct: number,
  map: PerformanceMapResponse,
  plotWidth: number,
  plotHeight: number
): MarkerGeometry {
  const { min: rpmMin, max: rpmMax } = map.axes.rpm;
  const tx = (rpm - rpmMin) / Math.max(1e-6, rpmMax - rpmMin);
  const ty = throttlePct / 100;
  const offMap = tx < -0.001 || tx > 1.001 || ty < -0.001 || ty > 1.001;
  return {
    x: Math.max(0, Math.min(1, tx)) * plotWidth,
    y: (1 - Math.max(0, Math.min(1, ty))) * plotHeight,
    offMap,
  };
}

function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(node);
    setWidth(node.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** The live engine's operating point, polled while the tab is visible. */
function useLiveOperatingPoint() {
  const [point, setPoint] = useState<LiveOperatingPoint | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      if (typeof document !== "undefined" && document.hidden) {
        timer = setTimeout(poll, LIVE_POLL_MS);
        return;
      }
      try {
        const next = await fetchLiveOperatingPoint();
        if (!cancelled) setPoint(next);
      } catch {
        // A marker that cannot be fetched is an absent marker, not an error banner: the
        // map itself is still entirely valid without it.
        if (!cancelled) setPoint(null);
      }
      if (!cancelled) timer = setTimeout(poll, LIVE_POLL_MS);
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return point;
}

export function PerformanceMapViewer() {
  const [metric, setMetric] = useState<PerformanceMetric>("power");
  const [altitudeInput, setAltitudeInput] = useState(2400);
  const [altitude, setAltitude] = useState(2400);
  const [maps, setMaps] = useState<MapsByMetric>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<TestBenchError | null>(null);
  const [hover, setHover] = useState<{ tx: number; ty: number } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [plotRef, plotWidth] = useElementWidth<HTMLDivElement>();

  const live = useLiveOperatingPoint();
  // The Operating-Point Optimizer panel itself lives on the Test Bench page (a
  // deliberately separate, no-WebSocket mode — see app/test-bench/page.tsx), but its
  // result is a plain zustand store, which is a global client-side singleton rather
  // than something scoped to whichever route mounted it. So this panel reads it exactly
  // as it would from any other store: run the optimizer once on the Test Bench, and its
  // recommendation keeps showing up here as the target marker until the app reloads.
  const optimizerResult = useTestBenchStore((s) => s.optimizerResult);

  // ---- altitude slider: debounce the drag, fetch the settled value ----------
  useEffect(() => {
    const t = setTimeout(() => setAltitude(altitudeInput), ALTITUDE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [altitudeInput]);

  // ---- fetch all three surfaces for this altitude --------------------------
  // They are computed together on the backend and memoised there, so the second and third
  // requests are dictionary lookups. Having all three in hand means the metric selector
  // never waits on the network, and the hover readout can show what the *other* two
  // metrics are doing at the point under the cursor — which is most of what makes a map
  // worth hovering.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all(
      METRICS.map((m) => fetchPerformanceMap({ altitude_m: altitude, metric: m.name }))
    )
      .then((responses) => {
        if (cancelled) return;
        const next: MapsByMetric = {};
        responses.forEach((r) => {
          next[r.metric] = r;
        });
        setMaps(next);
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setMaps({});
        setError(
          e instanceof TestBenchError
            ? e
            : new TestBenchError("Could not load the performance map", [], 0)
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [altitude]);

  const map = maps[metric] ?? null;

  const domain: ColorDomain | null = useMemo(
    () => (map ? colorDomain(map.z_min, map.z_max, map.metric, map.lower_is_better) : null),
    [map]
  );
  const levels = useMemo(() => (domain ? contourLevels(domain) : []), [domain]);

  const innerWidth = Math.max(0, plotWidth - MARGIN.left - MARGIN.right);
  const innerHeight = PLOT_HEIGHT - MARGIN.top - MARGIN.bottom;

  // ---- paint --------------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !map || !domain || innerWidth < 2) return;
    drawMap(canvas, {
      grid: map.z,
      domain,
      width: innerWidth,
      height: innerHeight,
      dpr: typeof window === "undefined" ? 1 : Math.min(2, window.devicePixelRatio || 1),
      levels,
    });
  }, [map, domain, levels, innerWidth, innerHeight]);

  // ---- markers ------------------------------------------------------------
  const liveMarker = useMemo(() => {
    if (!map || !live?.available || live.rpm === undefined || live.throttle_pct === undefined) {
      return null;
    }
    return {
      ...project(live.rpm, live.throttle_pct, map, innerWidth, innerHeight),
      rpm: live.rpm,
      throttlePct: live.throttle_pct,
      altitude: live.altitude_m ?? null,
    };
  }, [map, live, innerWidth, innerHeight]);

  const targetMarker = useMemo(() => {
    if (!map || !optimizerResult) return null;
    const rec = optimizerResult.recommended;
    return {
      ...project(rec.predicted.rpm, rec.setpoint.throttle_pct, map, innerWidth, innerHeight),
      rpm: rec.predicted.rpm,
      throttlePct: rec.setpoint.throttle_pct,
      altitude: optimizerResult.conditions.altitude_m,
      label: optimizerResult.objective_label,
      feasible: optimizerResult.recommended.feasible,
    };
  }, [map, optimizerResult, innerWidth, innerHeight]);

  const liveAltitudeMismatch =
    liveMarker?.altitude !== null &&
    liveMarker?.altitude !== undefined &&
    Math.abs(liveMarker.altitude - altitude) > ALTITUDE_MATCH_TOLERANCE_M;

  const targetAltitudeMismatch =
    targetMarker !== null &&
    Math.abs(targetMarker.altitude - altitude) > ALTITUDE_MATCH_TOLERANCE_M;

  // ---- hover readout ------------------------------------------------------
  const onPointerMove = useCallback(
    (event: React.PointerEvent<SVGRectElement>) => {
      const rect = event.currentTarget.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2) return;
      setHover({
        tx: (event.clientX - rect.left) / rect.width,
        ty: 1 - (event.clientY - rect.top) / rect.height,
      });
    },
    []
  );

  const hoverReadout = useMemo(() => {
    if (!hover || !map) return null;
    const { min: rpmMin, max: rpmMax } = map.axes.rpm;
    const rpm = rpmMin + hover.tx * (rpmMax - rpmMin);
    const throttlePct = hover.ty * 100;
    const values = METRICS.map((m) => {
      const source = maps[m.name];
      return {
        name: m.name,
        label: m.label,
        unit: source?.unit ?? m.short,
        value: source ? sampleNearest(source.z, hover.tx, hover.ty) : null,
      };
    });
    return {
      rpm,
      throttlePct,
      x: hover.tx * innerWidth,
      y: (1 - hover.ty) * innerHeight,
      values,
    };
  }, [hover, map, maps, innerWidth, innerHeight]);

  const rpmTicks = useMemo(() => {
    if (!map) return [];
    const { min, max } = map.axes.rpm;
    const ticks: number[] = [];
    const step = 500;
    for (let v = Math.ceil(min / step) * step; v <= max; v += step) ticks.push(v);
    if (ticks[0] !== min) ticks.unshift(min);
    if (ticks[ticks.length - 1] !== max) ticks.push(max);
    return ticks;
  }, [map]);

  const loadTicks = [0, 20, 40, 60, 80, 100];

  return (
    <GlassCard
      title="Engine Performance Map"
      subtitle="Steady-state dyno surface — crank speed held, every lag settled"
      glow="amber"
      bodyClassName="space-y-4 p-4"
      headerRight={
        <div className="flex items-center gap-2">
          {loading && <StatusPill tone="idle">Computing…</StatusPill>}
          {live?.available && !loading && (
            <StatusPill tone="go">{live.mission_recording ? "Recording" : "Live"}</StatusPill>
          )}
        </div>
      }
    >
      {/* ---- controls ---- */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[auto_1fr]">
        <div>
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
            Metric
          </span>
          <div className="inline-flex rounded-md border border-base-border bg-base-panel2/60 p-0.5">
            {METRICS.map((m) => (
              <button
                key={m.name}
                type="button"
                onClick={() => setMetric(m.name)}
                aria-pressed={metric === m.name}
                className={clsx(
                  "rounded px-3 py-1.5 text-xs font-medium transition-colors",
                  metric === m.name
                    ? "bg-status-cyan/15 text-status-cyan"
                    : "text-slate-400 hover:text-slate-200"
                )}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-baseline justify-between gap-3">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">
              Altitude — maps shift with air density
            </span>
            <span className="tabular text-sm text-status-cyan">
              {altitudeInput.toLocaleString()} m
              {map && (
                <span className="ml-2 text-[10px] text-slate-500">
                  σ {map.conditions.density_ratio.toFixed(3)} ·{" "}
                  {map.conditions.ambient_pressure_kpa.toFixed(0)} kPa
                </span>
              )}
            </span>
          </div>
          <input
            type="range"
            min={ALTITUDE_MIN}
            max={ALTITUDE_MAX}
            step={ALTITUDE_STEP}
            value={altitudeInput}
            onChange={(e) => setAltitudeInput(Number(e.target.value))}
            aria-label="Map altitude in metres"
            className="w-full accent-status-cyan"
          />
        </div>
      </div>

      {error ? (
        <div className="rounded-md border border-status-red/40 bg-status-red/10 px-3 py-2.5">
          <p className="text-xs font-medium text-status-red">{error.message}</p>
          {error.reasons.map((r) => (
            <p key={r} className="mt-1 text-[11px] text-slate-400">
              {r}
            </p>
          ))}
        </div>
      ) : (
        <>
          {/* ---- the map ---- */}
          <div
            ref={plotRef}
            className="relative w-full"
            style={{ height: PLOT_HEIGHT }}
          >
            {innerWidth > 2 && map && domain && (
              <>
                <canvas
                  ref={canvasRef}
                  className="pointer-events-none absolute rounded-sm"
                  style={{ left: MARGIN.left, top: MARGIN.top }}
                />
                <svg
                  className="absolute inset-0 h-full w-full"
                  role="img"
                  aria-label={`${map.metric_label} over crankshaft speed and throttle at ${altitude} metres`}
                >
                  <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
                    <rect
                      width={innerWidth}
                      height={innerHeight}
                      fill="none"
                      stroke="rgba(30,39,52,1)"
                    />

                    {/* axes */}
                    {rpmTicks.map((t) => {
                      const x =
                        ((t - map.axes.rpm.min) / (map.axes.rpm.max - map.axes.rpm.min)) *
                        innerWidth;
                      return (
                        <g key={`rpm-${t}`}>
                          <line
                            x1={x}
                            x2={x}
                            y1={innerHeight}
                            y2={innerHeight + 4}
                            stroke="#3b4757"
                          />
                          <text
                            x={x}
                            y={innerHeight + 15}
                            textAnchor="middle"
                            className="tabular"
                            fill="#64748b"
                            fontSize={9}
                          >
                            {t.toFixed(0)}
                          </text>
                        </g>
                      );
                    })}
                    <text
                      x={innerWidth / 2}
                      y={innerHeight + 29}
                      textAnchor="middle"
                      fill="#64748b"
                      fontSize={9}
                      letterSpacing="0.12em"
                    >
                      CRANKSHAFT SPEED (RPM)
                    </text>

                    {loadTicks.map((t) => {
                      const y = innerHeight * (1 - t / 100);
                      return (
                        <g key={`load-${t}`}>
                          <line x1={-4} x2={0} y1={y} y2={y} stroke="#3b4757" />
                          <text
                            x={-7}
                            y={y + 3}
                            textAnchor="end"
                            className="tabular"
                            fill="#64748b"
                            fontSize={9}
                          >
                            {t}
                          </text>
                        </g>
                      );
                    })}
                    <text
                      transform={`translate(${-MARGIN.left + 11},${innerHeight / 2}) rotate(-90)`}
                      textAnchor="middle"
                      fill="#64748b"
                      fontSize={9}
                      letterSpacing="0.12em"
                    >
                      THROTTLE / LOAD (%)
                    </text>

                    {/* the recommendation, drawn as the move it asks for */}
                    {liveMarker && targetMarker && (
                      <line
                        x1={liveMarker.x}
                        y1={liveMarker.y}
                        x2={targetMarker.x}
                        y2={targetMarker.y}
                        stroke={TARGET_COLOR}
                        strokeOpacity={0.5}
                        strokeWidth={1.5}
                        strokeDasharray="4 3"
                      />
                    )}

                    {targetMarker && (
                      <g transform={`translate(${targetMarker.x},${targetMarker.y})`}>
                        {/* Diamond, not a dot: the two markers must be separable without
                            relying on colour. */}
                        <path
                          d="M0,-9 L9,0 L0,9 L-9,0 Z"
                          fill="none"
                          stroke="#0a0e14"
                          strokeWidth={4}
                        />
                        <path
                          d="M0,-9 L9,0 L0,9 L-9,0 Z"
                          fill="none"
                          stroke={TARGET_COLOR}
                          strokeWidth={2}
                        />
                        <circle r={2} fill={TARGET_COLOR} />
                        <text
                          x={13}
                          y={-6}
                          fill={TARGET_COLOR}
                          fontSize={9}
                          letterSpacing="0.1em"
                          stroke="#0a0e14"
                          strokeWidth={3}
                          paintOrder="stroke"
                        >
                          TARGET
                        </text>
                      </g>
                    )}

                    {liveMarker && (
                      <g transform={`translate(${liveMarker.x},${liveMarker.y})`}>
                        <circle
                          r={11}
                          fill="none"
                          stroke={LIVE_COLOR}
                          strokeOpacity={0.35}
                          strokeWidth={2}
                          className="animate-pulseGlow"
                        />
                        <circle r={6} fill="#0a0e14" />
                        <circle r={4.5} fill={LIVE_COLOR} />
                        <text
                          x={13}
                          y={4}
                          fill={LIVE_COLOR}
                          fontSize={9}
                          letterSpacing="0.1em"
                          stroke="#0a0e14"
                          strokeWidth={3}
                          paintOrder="stroke"
                        >
                          LIVE
                        </text>
                      </g>
                    )}

                    {/* hover crosshair */}
                    {hoverReadout && (
                      <g pointerEvents="none">
                        <line
                          x1={hoverReadout.x}
                          x2={hoverReadout.x}
                          y1={0}
                          y2={innerHeight}
                          stroke="rgba(226,240,255,0.35)"
                          strokeDasharray="3 3"
                        />
                        <line
                          x1={0}
                          x2={innerWidth}
                          y1={hoverReadout.y}
                          y2={hoverReadout.y}
                          stroke="rgba(226,240,255,0.35)"
                          strokeDasharray="3 3"
                        />
                      </g>
                    )}

                    <rect
                      width={innerWidth}
                      height={innerHeight}
                      fill="transparent"
                      onPointerMove={onPointerMove}
                      onPointerLeave={() => setHover(null)}
                    />
                  </g>
                </svg>

                {hoverReadout && (
                  <div
                    className="pointer-events-none absolute z-10 min-w-[150px] rounded-md border border-base-border bg-base-panel/95 px-2.5 py-2 shadow-lg"
                    style={{
                      left: Math.min(
                        MARGIN.left + hoverReadout.x + 14,
                        MARGIN.left + innerWidth - 150
                      ),
                      top: Math.max(MARGIN.top, MARGIN.top + hoverReadout.y - 60),
                    }}
                  >
                    <div className="tabular mb-1.5 flex justify-between gap-3 text-[11px] text-slate-300">
                      <span>{hoverReadout.rpm.toFixed(0)} rpm</span>
                      <span>{hoverReadout.throttlePct.toFixed(0)} % load</span>
                    </div>
                    {hoverReadout.values.map((v) => (
                      <div
                        key={v.name}
                        className={clsx(
                          "flex justify-between gap-3 text-[10px]",
                          v.name === metric ? "text-status-cyan" : "text-slate-500"
                        )}
                      >
                        <span>{v.label}</span>
                        <span className="tabular">{formatMetric(v.value, v.unit)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>

          {/* ---- legend ---- */}
          {map && domain && (
            <div className="space-y-1.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[10px] uppercase tracking-wider text-slate-500">
                  {map.metric_label} ({map.unit})
                </span>
                <span className="text-[10px] text-slate-600">
                  {map.lower_is_better ? "bright = lower = better" : "bright = higher = better"}
                </span>
              </div>
              <div className="relative">
                <div
                  className="h-2.5 w-full rounded-sm"
                  style={{
                    background: `linear-gradient(to right, ${Array.from({ length: 12 }, (_, i) =>
                      rampColor(
                        normalise(domain.lo + ((domain.hi - domain.lo) * i) / 11, domain)
                      )
                    ).join(",")})`,
                  }}
                />
                {/* Tick marks sit at their value's position on the bar, not at even
                    spacing — the bar is a continuous scale, and a label that does not line
                    up with the colour it names is worse than no label. */}
                {legendTicks(levels).map((l) => {
                  const pct = ((l - domain.lo) / (domain.hi - domain.lo)) * 100;
                  if (pct < 9 || pct > 91) return null;
                  return (
                    <span
                      key={l}
                      className="absolute top-0 h-2.5 w-px bg-base-bg/60"
                      style={{ left: `${pct}%` }}
                    />
                  );
                })}
              </div>
              <div className="relative h-3">
                <span className="tabular absolute left-0 text-[9px] text-slate-500">
                  {domain.lo.toFixed(map.unit === "g/kWh" ? 0 : 1)}
                </span>
                {legendTicks(levels).map((l) => {
                  const pct = ((l - domain.lo) / (domain.hi - domain.lo)) * 100;
                  if (pct < 9 || pct > 91) return null;
                  return (
                    <span
                      key={l}
                      className="tabular absolute -translate-x-1/2 text-[9px] text-slate-500"
                      style={{ left: `${pct}%` }}
                    >
                      {l.toFixed(map.unit === "g/kWh" ? 0 : 1)}
                    </span>
                  );
                })}
                <span className="tabular absolute right-0 text-[9px] text-slate-500">
                  {domain.clipped ? "≥ " : ""}
                  {domain.hi.toFixed(map.unit === "g/kWh" ? 0 : 1)}
                </span>
              </div>
              <p className="text-[10px] leading-relaxed text-slate-600">
                Iso-lines step evenly across this scale; the ticks label a few of them.{" "}
                {map.description}
                {domain.clipped &&
                  " Values worse than the top of the scale are saturated, so the sweet spot stays readable."}
              </p>
            </div>
          )}

          {/* ---- markers & landmarks ---- */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MarkerCard
              swatch={<span className="h-2.5 w-2.5 rounded-full" style={{ background: LIVE_COLOR }} />}
              title="Live engine"
              lines={
                liveMarker
                  ? [
                      `${liveMarker.rpm.toFixed(0)} rpm · ${liveMarker.throttlePct.toFixed(0)} % throttle`,
                      liveMarker.altitude !== null
                        ? `at ${liveMarker.altitude.toFixed(0)} m${
                            liveAltitudeMismatch ? " — map is at a different altitude" : ""
                          }`
                        : "",
                      liveMarker.offMap ? "outside the mapped envelope — clamped to the edge" : "",
                    ]
                  : [live?.reason ?? "no live engine frame"]
              }
              tone={liveAltitudeMismatch ? "warn" : "normal"}
              action={
                liveMarker && liveAltitudeMismatch && liveMarker.altitude !== null
                  ? {
                      label: "Move map here",
                      onClick: () =>
                        setAltitudeInput(
                          Math.round(
                            Math.max(
                              ALTITUDE_MIN,
                              Math.min(ALTITUDE_MAX, liveMarker.altitude as number)
                            ) / ALTITUDE_STEP
                          ) * ALTITUDE_STEP
                        ),
                    }
                  : undefined
              }
            />

            <MarkerCard
              swatch={
                <span
                  className="h-2.5 w-2.5 rotate-45 border"
                  style={{ borderColor: TARGET_COLOR }}
                />
              }
              title="Optimizer target"
              lines={
                targetMarker
                  ? [
                      `${targetMarker.rpm.toFixed(0)} rpm · ${targetMarker.throttlePct.toFixed(0)} % throttle`,
                      `${targetMarker.label} · solved at ${targetMarker.altitude.toFixed(0)} m${
                        targetAltitudeMismatch ? " — map is at a different altitude" : ""
                      }`,
                      targetMarker.feasible ? "" : "recommendation is outside limits",
                    ]
                  : ["run the optimizer above to place it"]
              }
              tone={targetAltitudeMismatch ? "warn" : "normal"}
              action={
                targetMarker && targetAltitudeMismatch
                  ? {
                      label: "Move map here",
                      onClick: () =>
                        setAltitudeInput(
                          Math.round(
                            Math.max(
                              ALTITUDE_MIN,
                              Math.min(ALTITUDE_MAX, targetMarker.altitude)
                            ) / ALTITUDE_STEP
                          ) * ALTITUDE_STEP
                        ),
                    }
                  : undefined
              }
            />

            <MarkerCard
              title="Peak power on this map"
              lines={
                map?.landmarks.peak_power
                  ? [
                      `${map.landmarks.peak_power.power_kw.toFixed(1)} kW`,
                      `${map.landmarks.peak_power.rpm.toFixed(0)} rpm · ${map.landmarks.peak_power.throttle_pct.toFixed(0)} % throttle`,
                      `MAP ${map.landmarks.peak_power.manifold_pressure_kpa.toFixed(0)} kPa`,
                    ]
                  : ["—"]
              }
            />

            <MarkerCard
              title="Best BSFC on this map"
              lines={
                map?.landmarks.best_bsfc
                  ? [
                      `${map.landmarks.best_bsfc.bsfc_g_per_kwh?.toFixed(0) ?? "—"} g/kWh`,
                      `${map.landmarks.best_bsfc.rpm.toFixed(0)} rpm · ${map.landmarks.best_bsfc.throttle_pct.toFixed(0)} % throttle`,
                      `${map.landmarks.best_bsfc.power_kw.toFixed(1)} kW at that point`,
                    ]
                  : ["—"]
              }
            />
          </div>

          {map && (
            <p className="text-[10px] leading-relaxed text-slate-600">
              {map.assumptions.join(" ")}
            </p>
          )}
        </>
      )}
    </GlassCard>
  );
}

function MarkerCard({
  swatch,
  title,
  lines,
  tone = "normal",
  action,
}: {
  swatch?: React.ReactNode;
  title: string;
  lines: string[];
  tone?: "normal" | "warn";
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div
      className={clsx(
        "rounded-lg border px-3 py-2.5",
        tone === "warn"
          ? "border-status-amber/40 bg-status-amber/5"
          : "border-base-border/70 bg-base-panel2/40"
      )}
    >
      <span className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
        {swatch}
        {title}
      </span>
      {lines
        .filter(Boolean)
        .map((line, i) => (
          <span
            // Index key: the lines are a fixed, ordered readout, and two of them can
            // legitimately carry the same text.
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            className={clsx(
              "tabular block leading-snug",
              i === 0 ? "text-sm text-slate-200" : "text-[10px] text-slate-500"
            )}
          >
            {line}
          </span>
        ))}
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="mt-1.5 rounded border border-status-amber/50 px-2 py-0.5 text-[10px] text-status-amber hover:bg-status-amber/10"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
