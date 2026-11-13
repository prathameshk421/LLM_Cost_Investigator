import { useMemo } from "react";
import { fmtTimeUtc } from "./SpendFormat";
import { SpendStackedBars } from "./SpendStackedBars";

export interface LiveBucket {
  /** ISO time of the bucket start. */
  t: string;
  by_feature: Record<string, number>;
}

/** Rolling stacked bars. Pass at most ~150 buckets (5 min at 2 s). */
export function LiveSpendChart({ buckets, features }: { buckets: LiveBucket[]; features: string[] }) {
  const series = useMemo(() => {
    const out: Record<string, number[]> = {};
    for (const f of features) out[f] = buckets.map((b) => b.by_feature[f] ?? 0);
    return out;
  }, [buckets, features]);
  const times = useMemo(() => buckets.map((b) => b.t), [buckets]);

  return (
    <SpendStackedBars
      buckets={times}
      series={series}
      features={features}
      height={220}
      xLabel={(t) => fmtTimeUtc(t).slice(3, 8)}
      tooltipTitle={(t) => `${fmtTimeUtc(t)} (2 s bucket)`}
      ariaLabel="Live spend per 2 second bucket over the last 5 minutes, stacked by feature"
      emptyText="Waiting for ingest events. Start demo traffic or send events to /v1/ingest."
    />
  );
}
