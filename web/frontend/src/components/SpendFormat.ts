// Shared helpers for the Spend and Live views.

// Categorical slots in fixed order, same as CostTimeline.
export const SPEND_PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9", "#e66767", "#008300"];
export const SPEND_OTHER_COLOR = "#64748b";

export function colorFor(keys: string[], key: string): string {
  const i = keys.indexOf(key);
  if (i < 0 || i >= SPEND_PALETTE.length) return SPEND_OTHER_COLOR;
  return SPEND_PALETTE[i];
}

export function fmtUsd(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "$0";
  const a = Math.abs(v);
  const s = v < 0 ? "-" : "";
  if (a === 0) return "$0";
  if (a < 0.01) return `${s}$${a.toFixed(4)}`;
  if (a < 1) return `${s}$${a.toFixed(3)}`;
  if (a < 1000) return `${s}$${a.toFixed(2)}`;
  return `${s}$${Math.round(a).toLocaleString("en-US")}`;
}

export function fmtSigned(v: number): string {
  if (!Number.isFinite(v) || v === 0) return "$0";
  return `${v > 0 ? "+" : "-"}${fmtUsd(Math.abs(v))}`;
}

export function fmtNum(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "0";
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(1)}k`;
  return Math.round(v).toLocaleString("en-US");
}

export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "0%";
  return `${v.toFixed(digits)}%`;
}

export function fmtMs(v: number): string {
  if (!Number.isFinite(v)) return "0 ms";
  return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;
}

export function fmtDateShort(iso: string, hourly: boolean): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (hourly) return d.toISOString().slice(11, 16);
  return d.toISOString().slice(5, 10);
}

export function fmtTimeUtc(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(11, 19) + "Z";
}

export function niceStep(v: number): number {
  if (!(v > 0)) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / exp;
  const m = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return m * exp;
}

export const HYPOTHESIS_LABEL: Record<string, string> = {
  uncapped_retry_loop: "Retry loop",
  context_bloat_self_calling_agent: "Context bloat",
  expensive_model_misroute: "Model misroute",
  no_strong_signal: "No strong signal",
};

export function hypothesisLabel(h: string): string {
  return HYPOTHESIS_LABEL[h] ?? h.replace(/_/g, " ");
}

export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
