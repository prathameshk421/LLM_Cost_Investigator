import { useEffect, useMemo, useRef, useState } from "react";
import type { CostTimelineData } from "../types/investigate";

// Categorical slots, fixed order. Validated on the #0c1016 surface
// (dataviz validate_palette.js: all six checks pass for 5 slots).
const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9", "#e66767", "#008300"];

const HYPOTHESIS_LABEL: Record<string, string> = {
  uncapped_retry_loop: "retry loop",
  context_bloat_self_calling_agent: "context bloat",
  expensive_model_misroute: "model misroute",
  no_strong_signal: "no strong signal",
};

const H = 240;
const PAD = { top: 16, right: 16, bottom: 28, left: 56 };

function fmtUsd(v: number): string {
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  if (v < 1) return `$${v.toFixed(3)}`;
  return `$${v.toFixed(2)}`;
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toISOString().slice(11, 16);
}

// Round tick step (1, 2, 2.5 or 5 × 10^k) so labels read as clean numbers.
function niceStep(v: number): number {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / exp;
  const m = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return m * exp;
}

export function CostTimeline({ data }: { data: CostTimelineData }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(320, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const features = useMemo(() => Object.keys(data.series), [data]);
  // Color follows the feature (its index in the full list), never its visibility.
  const colorOf = (f: string) => SERIES[features.indexOf(f) % SERIES.length];
  const visible = features.filter((f) => !hidden.has(f));

  const n = data.windows.length;
  const innerW = width - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const rawMax = Math.max(0, ...visible.flatMap((f) => data.series[f]));
  const step = niceStep(rawMax / 4);
  const maxY = Math.max(step, Math.ceil(rawMax / step) * step);
  const x = (i: number) => PAD.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const y = (v: number) => PAD.top + innerH - (v / maxY) * innerH;
  const ticks = Array.from({ length: Math.round(maxY / step) + 1 }, (_, i) => i * step);
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innerW / 70))));

  const flaggedIdx = data.flagged
    .map((f) => ({ ...f, i: data.windows.indexOf(f.start) }))
    .filter((f) => f.i >= 0);
  // One band per window, colored by the most severe (non-"no signal") finding.
  const bands = new Map<number, (typeof flaggedIdx)[number]>();
  for (const f of flaggedIdx) {
    const prev = bands.get(f.i);
    if (!prev || (prev.hypothesis === "no_strong_signal" && f.hypothesis !== "no_strong_signal")) {
      bands.set(f.i, f);
    }
  }
  const slot = n <= 1 ? innerW : innerW / (n - 1);

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left - PAD.left;
    const i = Math.round((px / innerW) * (n - 1));
    setHover(i >= 0 && i < n ? i : null);
  }

  const hoverBand = hover !== null ? bands.get(hover) : undefined;

  return (
    <div className="timeline-card" ref={hostRef}>
      <div className="timeline-head">
        <div>
          <div className="label">Cost per {data.window_minutes}-min window</div>
          <div className="timeline-sub">
            Shaded bands are the windows the detector flagged. Hover for values.
          </div>
        </div>
        <div className="timeline-legend" role="list">
          {features.map((f) => (
            <button
              key={f}
              type="button"
              role="listitem"
              className={"legend-item" + (hidden.has(f) ? " off" : "")}
              onClick={() =>
                setHidden((prev) => {
                  const next = new Set(prev);
                  if (next.has(f)) next.delete(f);
                  else next.add(f);
                  return next;
                })
              }
              aria-pressed={!hidden.has(f)}
              title={hidden.has(f) ? `Show ${f}` : `Hide ${f}`}
            >
              <span className="legend-swatch" style={{ background: colorOf(f) }} />
              {f}
            </button>
          ))}
        </div>
      </div>

      <div style={{ position: "relative" }}>
        <svg
          width={width}
          height={H}
          role="img"
          aria-label="Cost per feature over time, with flagged anomaly windows"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          style={{ display: "block" }}
        >
          {[...bands.values()].map((b) => {
            const strong = b.hypothesis !== "no_strong_signal";
            return (
              <g key={`band-${b.i}`}>
                <rect
                  x={x(b.i) - slot / 2}
                  y={PAD.top}
                  width={slot}
                  height={innerH}
                  className={strong ? "band-strong" : "band-weak"}
                />
              </g>
            );
          })}

          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid-line" />
              <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="axis-text">
                {fmtUsd(t)}
              </text>
            </g>
          ))}

          {data.windows.map((w, i) =>
            i % labelEvery === 0 ? (
              <text key={w} x={x(i)} y={H - 8} textAnchor="middle" className="axis-text">
                {fmtTime(w)}
              </text>
            ) : null
          )}

          {visible.map((f) => {
            const pts = data.series[f].map((v, i) => `${x(i)},${y(v)}`).join(" ");
            return (
              <polyline
                key={f}
                points={pts}
                fill="none"
                stroke={colorOf(f)}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            );
          })}

          {hover !== null ? (
            <>
              <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} className="crosshair" />
              {visible.map((f) => (
                <circle
                  key={f}
                  cx={x(hover)}
                  cy={y(data.series[f][hover])}
                  r={4}
                  fill={colorOf(f)}
                  stroke="var(--bg-1)"
                  strokeWidth={2}
                />
              ))}
            </>
          ) : null}
        </svg>

        {hover !== null ? (
          <div
            className="chart-tooltip"
            style={{
              left: Math.min(x(hover) + 12, width - 220),
              top: PAD.top,
            }}
          >
            <div className="mono" style={{ color: "var(--text-1)", marginBottom: 4 }}>
              {fmtTime(data.windows[hover])} · {data.calls_per_window[hover]} calls
            </div>
            {visible
              .map((f) => ({ f, v: data.series[f][hover] }))
              .sort((a, b) => b.v - a.v)
              .map(({ f, v }) => (
                <div key={f} className="tooltip-row">
                  <span className="legend-swatch" style={{ background: colorOf(f) }} />
                  <span>{f}</span>
                  <span className="mono">{fmtUsd(v)}</span>
                </div>
              ))}
            {hoverBand ? (
              <div className="tooltip-flag">
                flagged · {hoverBand.feature_tag} ·{" "}
                {HYPOTHESIS_LABEL[hoverBand.hypothesis] ?? hoverBand.hypothesis}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
