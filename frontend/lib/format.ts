/** Small formatting helpers shared across dashboard components. */

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

export function formatRul(minutes: number | null): string {
  if (minutes === null) return "—";
  if (minutes < 1) return "<1 min";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return `${h}h ${m}m`;
}

export function formatTimeHHMMSS(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

export type BandStatus = "normal" | "warn" | "critical";

export function bandStatus(value: number, band: { min: number; max: number }): BandStatus {
  const span = band.max - band.min;
  const warnMargin = span * 0.08;
  if (value < band.min - warnMargin || value > band.max + warnMargin) return "critical";
  if (value < band.min || value > band.max) return "warn";
  return "normal";
}
