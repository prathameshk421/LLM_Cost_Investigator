import { useEffect, useMemo, useRef, useState } from "react";
import { colorFor, fmtUsd, niceStep } from "./SpendFormat";

export interface StackedBarsProps {
  /** One entry per x bucket, ISO time or any label source. */
  buckets: string[];
  /** feature -> values aligned with buckets. */
  series: Record<string, number[]>;
  /** Fixed feature order; color follows the index here. */
  features: string[];
  xLabel: (bucket: string) => string;
  tooltipTitle: (bucket: string) => string;
  height?: number;
  ariaLabel: string;
  emptyText?: string;
  showLegend?: boolean;
}

const PAD = { top: 12, right: 12, bottom: 26, left: 56 };

export function SpendStackedBars({
  buckets,
  series,
  features,
  xLabel,
  tooltipTitle,
  height = 240,
  ariaLabel,
  emptyText = "No spend in this range.",
  showLegend = true,
}: StackedBarsProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(260, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const n = buckets.length;
  const visible = useMemo(() => features.filter((f) => !hidden.has(f) && series[f]), [features, hidden, series]);
  const val = (f: string, i: number) => {
    const v = series[f]?.[i];
    return typeof v === "number" && Number.isFinite(v) ? v : 0;
  };

  const totals = useMemo(
    () => buckets.map((_, i) => visible.reduce((s, f) => s + val(f, i), 0)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [buckets, visible, series]
  );
  const rawMax = Math.max(0, ...totals);
  const step = niceStep(rawMax / 4);
  const maxY = Math.max(step, Math.ceil(rawMax / step) * step);
  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;
  const slot = n > 0 ? innerW / n : innerW;
  const barW = Math.max(1, slot * 0.78);
  const y = (v: number) => PAD.top + innerH - (v / maxY) * innerH;
  const ticks = Array.from({ length: Math.round(maxY / step) + 1 }, (_, i) => i * step);
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innerW / 64))));

  function onMove(clientX: number, el: SVGSVGElement) {
    const rect = el.getBoundingClientRect();
    const i = Math.floor((clientX - rect.left - PAD.left) / slot);
    setHover(i >= 0 && i < n ? i : null);
  }

  function onKey(e: React.KeyboardEvent) {
    if (n === 0) return;
    if (e.key === "ArrowRight") setHover((h) => (h === null ? 0 : Math.min(n - 1, h + 1)));
    else if (e.key === "ArrowLeft") setHover((h) => (h === null ? n - 1 : Math.max(0, h - 1)));
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  }

  const hoverCenter = hover !== null ? PAD.left + (hover + 0.5) * slot : 0;
  const tipLeft = hover !== null ? (hoverCenter > width / 2 ? Math.max(0, hoverCenter - 214) : hoverCenter + 12) : 0;

  return (
    <div className="sp-chart" ref={hostRef}>
      {showLegend ? (
        <div className="sp-legend" role="group" aria-label="Toggle features">
          {features.map((f) => (
            <button
              key={f}
              type="button"
              className={"legend-item" + (hidden.has(f) ? " off" : "")}
              aria-pressed={!hidden.has(f)}
              title={hidden.has(f) ? `Show ${f}` : `Hide ${f}`}
              onClick={() =>
                setHidden((prev) => {
                  const next = new Set(prev);
                  if (next.has(f)) next.delete(f);
                  else next.add(f);
                  return next;
                })
              }
            >
              <span className="legend-swatch" style={{ background: colorFor(features, f) }} />
              {f}
            </button>
          ))}
        </div>
      ) : null}

      {n === 0 || features.length === 0 ? (
        <div className="sp-empty-inline">{emptyText}</div>
      ) : (
        <div style={{ position: "relative" }}>
          <svg
            width={width}
            height={height}
            role="img"
            aria-label={ariaLabel}
            tabIndex={0}
            onMouseMove={(e) => onMove(e.clientX, e.currentTarget)}
            onMouseLeave={() => setHover(null)}
            onTouchStart={(e) => onMove(e.touches[0].clientX, e.currentTarget)}
            onTouchMove={(e) => onMove(e.touches[0].clientX, e.currentTarget)}
            onKeyDown={onKey}
            onBlur={() => setHover(null)}
            style={{ display: "block", maxWidth: "100%" }}
          >
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid-line" />
                <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="axis-text">
                  {fmtUsd(t)}
                </text>
              </g>
            ))}
            {buckets.map((b, i) =>
              i % labelEvery === 0 ? (
                <text key={b + i} x={PAD.left + (i + 0.5) * slot} y={height - 8} textAnchor="middle" className="axis-text">
                  {xLabel(b)}
                </text>
              ) : null
            )}
            {buckets.map((b, i) => {
              let acc = 0;
              return (
                <g key={b + i} opacity={hover === null || hover === i ? 1 : 0.55}>
                  {visible.map((f) => {
                    const v = val(f, i);
                    if (v <= 0) return null;
                    const y0 = y(acc);
                    acc += v;
                    const y1 = y(acc);
                    return (
                      <rect
                        key={f}
                        x={PAD.left + i * slot + (slot - barW) / 2}
                        y={y1}
                        width={barW}
                        height={Math.max(0, y0 - y1 - (barW > 4 ? 0.5 : 0))}
                        fill={colorFor(features, f)}
                      />
                    );
                  })}
                </g>
              );
            })}
            {hover !== null ? (
              <line x1={hoverCenter} x2={hoverCenter} y1={PAD.top} y2={PAD.top + innerH} className="crosshair" />
            ) : null}
          </svg>

          {hover !== null ? (
            <div className="chart-tooltip" style={{ left: tipLeft, top: PAD.top }} role="status">
              <div className="mono" style={{ color: "var(--text-1)", marginBottom: 4 }}>
                {tooltipTitle(buckets[hover])}
              </div>
              {visible
                .map((f) => ({ f, v: val(f, hover) }))
                .filter((r) => r.v > 0)
                .sort((a, b) => b.v - a.v)
                .map(({ f, v }) => (
                  <div key={f} className="tooltip-row">
                    <span className="legend-swatch" style={{ background: colorFor(features, f) }} />
                    <span>{f}</span>
                    <span className="mono">{fmtUsd(v)}</span>
                  </div>
                ))}
              <div className="tooltip-row sp-tip-total">
                <span />
                <span>Total</span>
                <span className="mono">{fmtUsd(totals[hover])}</span>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
