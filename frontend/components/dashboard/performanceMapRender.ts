/**
 * Drawing helpers for the engine performance map.
 *
 * Kept out of the component for the same reason `engine3dUtils.ts` is: this is pure
 * arithmetic over a grid — colour domains, bilinear sampling, marching-squares contours —
 * and it is far easier to reason about (and to fix) when it is not interleaved with React
 * state and layout.
 *
 * The surface is drawn on a canvas rather than in SVG because it is a *field*, not a set
 * of marks: 30x30 cells upsampled to a few hundred thousand pixels is one `ImageData`
 * write, where the same thing as SVG rects would be ~900 nodes that still look like a
 * mosaic. Contours come from d3-contour (proper marching squares with polygon assembly)
 * and are stroked onto the same canvas, so the layer order — field, then iso-lines, then
 * the undefined mask — is simply the order the calls happen in. Everything that needs to
 * be hit-tested, labelled or animated (axes, markers, hover) stays in SVG on top.
 */
import { contours as d3contours } from "d3-contour";

export type Grid = (number | null)[][];

/**
 * Sequential ramp, one hue, dark -> bright.
 *
 * A single hue rather than a rainbow: rainbow ramps invent boundaries that are not in the
 * data, which on a performance map would read as engine behaviour that is not there. On a
 * dark surface the low end is the step that recedes toward the panel background, so "near
 * nothing" looks like nothing, and magnitude reads as light.
 *
 * The same ramp serves all three metrics, and the *direction* carries the sense: for BSFC,
 * where lower is better, the domain is reversed so the bright end always means "better for
 * the quantity named". The legend prints the numbers, so the direction is never something
 * the reader has to infer from the colour alone.
 *
 * Validated as a ramp, not as a categorical palette: lightness is monotone, every adjacent
 * step clears the 0.06 lightness gap, and the hue spread is 19° — inside the 40° that makes
 * it genuinely one hue. (An earlier version drifted 57°, from navy at the bottom to cyan at
 * the top, which is a two-hue ramp wearing one name.) The darkest step sits at 1.0:1 against
 * the panel surface on purpose: for a *continuous* field the near-zero end is supposed to
 * recede into the background, which is the one place the discrete-ramp contrast floor does
 * not apply. Undefined cells are not on this ramp at all — they are hatched.
 */
// Flat typed arrays rather than nested tuples: the pixel loop below reads these a few
// hundred thousand times per repaint, and a flat buffer indexes without allocating.
// `noUncheckedIndexedAccess` types every read as possibly-undefined, so the reads carry
// `!` — every index in this file is derived from a clamped floor or an explicit loop
// bound, so none of them can be out of range.
const RAMP_POSITIONS = new Float64Array([0.0, 0.2, 0.4, 0.6, 0.8, 1.0]);
const RAMP_RGB = new Float64Array([
  11, 26, 31,     // #0b1a1f
  13, 53, 64,     // #0d3540
  15, 90, 104,    // #0f5a68
  18, 138, 157,   // #128a9d
  47, 189, 208,   // #2fbdd0
  127, 233, 242,  // #7fe9f2
]);

/** Cells where the metric is undefined — BSFC below the idle power floor. */
export const UNDEFINED_FILL = "#0b111c";
export const UNDEFINED_HATCH = "rgba(91,107,130,0.28)";

function buildLut(): Uint8ClampedArray {
  const stops = RAMP_POSITIONS.length;
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i += 1) {
    const t = i / 255;
    let k = 0;
    while (k < stops - 2 && t > RAMP_POSITIONS[k + 1]!) k += 1;
    const t0 = RAMP_POSITIONS[k]!;
    const t1 = RAMP_POSITIONS[k + 1]!;
    const f = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
    for (let c = 0; c < 3; c += 1) {
      const a = RAMP_RGB[k * 3 + c]!;
      const b = RAMP_RGB[(k + 1) * 3 + c]!;
      lut[i * 3 + c] = a + (b - a) * f;
    }
  }
  return lut;
}

const LUT = buildLut();

export function rampColor(t: number): string {
  const i = Math.max(0, Math.min(255, Math.round(t * 255)));
  return `rgb(${LUT[i * 3]!},${LUT[i * 3 + 1]!},${LUT[i * 3 + 2]!})`;
}

/**
 * BSFC diverges as power approaches zero, so the raw maximum is set by the least
 * interesting corner of the map. Linear-scaling to it flattens the entire useful range
 * into two or three shades and hides the island the chart exists to show.
 *
 * Clipping the top of the domain to a multiple of the best value is what real BSFC maps
 * do — they contour 240, 260, 300, 400 and let everything worse fall off the scale. The
 * legend marks the clipped end with a "≥", so nothing is silently misrepresented.
 *
 * 1.5x was picked by rendering it: at 2.2x the useful band (roughly best to best+30%) was
 * squeezed into the top fifth of the ramp and the whole mid-map read as one flat colour,
 * which is exactly the failure the island is supposed to be visible against. 1.5x spans
 * the band an operator actually chooses between and lets the low-load corner saturate.
 */
export const BSFC_DOMAIN_FACTOR = 1.5;

export interface ColorDomain {
  lo: number;
  hi: number;
  /** True when values worse than `hi` exist and are saturated at the scale's dull end. */
  clipped: boolean;
  /** BSFC: the bright end is the *low* end. */
  reversed: boolean;
}

export function colorDomain(
  zMin: number | null,
  zMax: number | null,
  metric: string,
  lowerIsBetter: boolean
): ColorDomain | null {
  if (zMin === null || zMax === null || !Number.isFinite(zMin) || !Number.isFinite(zMax)) {
    return null;
  }
  if (metric === "bsfc") {
    const hi = Math.min(zMax, zMin * BSFC_DOMAIN_FACTOR);
    return { lo: zMin, hi: Math.max(hi, zMin * 1.05), clipped: zMax > hi + 1e-6, reversed: true };
  }
  // Power is anchored at zero rather than at its own minimum: the flat region below the
  // friction line genuinely *is* zero output, and starting the scale there keeps the
  // "nothing happening down here" corner reading as nothing.
  const lo = metric === "power" ? 0 : zMin;
  const hi = zMax > lo ? zMax : lo + 1;
  return { lo, hi, clipped: false, reversed: lowerIsBetter };
}

export function normalise(value: number, domain: ColorDomain): number {
  const t = (value - domain.lo) / (domain.hi - domain.lo);
  const clamped = Math.max(0, Math.min(1, t));
  return domain.reversed ? 1 - clamped : clamped;
}

/** "Nice" contour levels strictly inside the domain — the same values the legend ticks. */
export function contourLevels(domain: ColorDomain, target = 7): number[] {
  const span = domain.hi - domain.lo;
  if (!(span > 0)) return [];
  // d3's tick ladder: the sqrt midpoints (sqrt(50), sqrt(10), sqrt(2)) pick the 1/2/5/10
  // step whose *ratio* error is smallest, which is what keeps the line count near the
  // target instead of overshooting it by half again.
  const rough = span / (target + 1);
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalised = rough / magnitude;
  const step =
    magnitude *
    (normalised >= Math.sqrt(50)
      ? 10
      : normalised >= Math.sqrt(10)
        ? 5
        : normalised >= Math.sqrt(2)
          ? 2
          : 1);
  const levels: number[] = [];
  for (let v = Math.ceil(domain.lo / step) * step; v < domain.hi; v += step) {
    if (v > domain.lo + span * 0.02) levels.push(Number(v.toFixed(6)));
  }
  return levels;
}

/**
 * Flatten the grid for d3-contour, substituting the worst in-domain value for undefined
 * cells.
 *
 * Marching squares has no concept of a hole, and leaving NaN there produces iso-lines that
 * wander into the gap. Undefined only ever means "BSFC below the idle power floor", where
 * the true value is diverging upward — so continuing the field at the dull end of the
 * scale is the physically right closure, and the mask drawn afterwards covers that region
 * anyway.
 */
interface PreparedGrid {
  nx: number;
  ny: number;
  /** Row-major, load-ascending. Undefined cells carry the domain's dull end. */
  values: Float64Array;
  /** 1 where the metric has a value, 0 where it does not. */
  defined: Uint8Array;
}

/**
 * Flatten the grid once, substituting the worst in-domain value for undefined cells.
 *
 * Marching squares has no concept of a hole, and leaving NaN there produces iso-lines that
 * wander into the gap. Undefined only ever means "BSFC below the idle power floor", where
 * the true value is diverging upward — so continuing the field at the dull end of the
 * scale is the physically right closure, and the mask drawn afterwards covers that region
 * anyway. The separate `defined` bitmap is what the mask and the pixel loop read.
 */
function prepareGrid(grid: Grid, domain: ColorDomain): PreparedGrid | null {
  const ny = grid.length;
  const nx = grid[0]?.length ?? 0;
  if (nx < 2 || ny < 2) return null;
  const filler = domain.reversed ? domain.hi : domain.lo;
  const values = new Float64Array(nx * ny);
  const defined = new Uint8Array(nx * ny);
  for (let y = 0; y < ny; y += 1) {
    const row = grid[y];
    for (let x = 0; x < nx; x += 1) {
      const v = row?.[x];
      const ok = v !== null && v !== undefined && Number.isFinite(v);
      values[y * nx + x] = ok ? (v as number) : filler;
      defined[y * nx + x] = ok ? 1 : 0;
    }
  }
  return { nx, ny, values, defined };
}

export interface DrawOptions {
  grid: Grid;
  domain: ColorDomain;
  width: number;
  height: number;
  dpr: number;
  levels: number[];
}

/** Paint the field, the iso-lines and the undefined mask, in that order. */
export function drawMap(canvas: HTMLCanvasElement, opts: DrawOptions): void {
  const { grid, domain, width, height, dpr, levels } = opts;
  if (width < 2 || height < 2) return;
  const prepared = prepareGrid(grid, domain);
  if (!prepared) return;
  const { nx, ny, values, defined } = prepared;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const pw = Math.round(width * dpr);
  const ph = Math.round(height * dpr);
  canvas.width = pw;
  canvas.height = ph;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  // ---- 1. the field, bilinear ------------------------------------------------
  const image = ctx.createImageData(pw, ph);
  const data = image.data;
  const [ur, ug, ub] = hexToRgb(UNDEFINED_FILL);

  for (let py = 0; py < ph; py += 1) {
    // Screen y runs downward; load runs upward, so the axis is flipped here.
    const fy = (1 - (py + 0.5) / ph) * (ny - 1);
    const y0 = Math.max(0, Math.min(ny - 2, Math.floor(fy)));
    const wy = fy - y0;
    for (let px = 0; px < pw; px += 1) {
      const fx = ((px + 0.5) / pw) * (nx - 1);
      const x0 = Math.max(0, Math.min(nx - 2, Math.floor(fx)));
      const wx = fx - x0;

      const i00 = y0 * nx + x0;
      const i01 = (y0 + 1) * nx + x0;
      const o = (py * pw + px) * 4;
      data[o + 3] = 255;

      if (
        !defined[i00] ||
        !defined[i00 + 1] ||
        !defined[i01] ||
        !defined[i01 + 1]
      ) {
        // Any undefined corner makes the interpolated value meaningless, so the whole
        // pixel is undefined. This is what gives the mask its soft, cell-aligned edge.
        data[o] = ur;
        data[o + 1] = ug;
        data[o + 2] = ub;
        continue;
      }

      const value =
        values[i00]! * (1 - wx) * (1 - wy) +
        values[i00 + 1]! * wx * (1 - wy) +
        values[i01]! * (1 - wx) * wy +
        values[i01 + 1]! * wx * wy;
      const idx =
        Math.max(0, Math.min(255, Math.round(normalise(value, domain) * 255))) * 3;
      data[o] = LUT[idx]!;
      data[o + 1] = LUT[idx + 1]!;
      data[o + 2] = LUT[idx + 2]!;
    }
  }
  ctx.putImageData(image, 0, 0);

  // ---- 2. iso-lines ----------------------------------------------------------
  const generator = d3contours().size([nx, ny]).thresholds(levels);
  const sx = width / (nx - 1);
  const sy = height / (ny - 1);
  // d3-contour reports coordinates in grid space with the *cell centre* convention: the
  // value at index i sits at coordinate i + 0.5, and a ring may run half a cell past the
  // edge. Subtracting that half cell is what lines the iso-lines up with the field
  // underneath — without it every contour sits ~1/60th of the plot to the right of the
  // colour it bounds, which is small enough to look like an artifact and wrong enough to
  // misplace the BSFC island's edge.
  const gridToX = (gx: number) => (gx - 0.5) * sx;
  const gridToY = (gy: number) => height - (gy - 0.5) * sy;

  ctx.save();
  ctx.lineJoin = "round";
  ctx.strokeStyle = "rgba(226,240,255,0.30)";
  ctx.lineWidth = 1;
  for (const contour of generator(Array.from(values))) {
    const path = new Path2D();
    for (const polygon of contour.coordinates) {
      for (const ring of polygon) {
        ring.forEach((point, i) => {
          const x = gridToX(point[0] ?? 0);
          // Same vertical flip as the field: grid row 0 is the bottom of the plot.
          const y = gridToY(point[1] ?? 0);
          if (i === 0) path.moveTo(x, y);
          else path.lineTo(x, y);
        });
        path.closePath();
      }
    }
    ctx.stroke(path);
  }
  ctx.restore();

  // ---- 3. mask the undefined region -----------------------------------------
  // Redrawn on top so no iso-line appears to cross a region where the metric has no
  // value. Hatching, not a flat fill, so it reads as "no data" rather than as a low
  // reading — the one thing a heatmap must never blur.
  ctx.save();
  ctx.beginPath();
  let masked = false;
  for (let y = 0; y < ny - 1; y += 1) {
    for (let x = 0; x < nx - 1; x += 1) {
      const i = y * nx + x;
      if (defined[i] && defined[i + 1] && defined[i + nx] && defined[i + nx + 1]) continue;
      ctx.rect(x * sx, height - (y + 1) * sy, sx + 0.5, sy + 0.5);
      masked = true;
    }
  }
  if (masked) {
    ctx.clip();
    ctx.fillStyle = UNDEFINED_FILL;
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = UNDEFINED_HATCH;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let d = -height; d < width; d += 7) {
      ctx.moveTo(d, height);
      ctx.lineTo(d + height, 0);
    }
    ctx.stroke();
  }
  ctx.restore();
}

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Grid value at the cell nearest a fractional (rpm, load) position. Null when undefined. */
export function sampleNearest(grid: Grid, tx: number, ty: number): number | null {
  const ny = grid.length;
  const nx = grid[0]?.length ?? 0;
  if (!nx || !ny) return null;
  const x = Math.max(0, Math.min(nx - 1, Math.round(tx * (nx - 1))));
  const y = Math.max(0, Math.min(ny - 1, Math.round(ty * (ny - 1))));
  return grid[y]?.[x] ?? null;
}

export function formatMetric(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const decimals = unit === "g/kWh" ? 0 : value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(decimals)} ${unit}`;
}

/**
 * At most `max` labels for the colour bar, evenly picked from the contour levels.
 *
 * The lines and their labels have different budgets: a dozen iso-lines read fine on the
 * surface, but a dozen numbers under a 400 px bar collide. Sampling the same list keeps
 * every label a value that is actually drawn.
 */
export function legendTicks(levels: number[], max = 5): number[] {
  if (levels.length <= max) return levels;
  const stride = (levels.length - 1) / (max - 1);
  const picked: number[] = [];
  for (let i = 0; i < max; i += 1) {
    const value = levels[Math.round(i * stride)];
    if (value !== undefined && !picked.includes(value)) picked.push(value);
  }
  return picked;
}
