import { useMemo } from "react";
import type { SpendSeries } from "../types/analytics";
import { fmtDateShort } from "./SpendFormat";
import { SpendStackedBars } from "./SpendStackedBars";

/** Stacked bars of spend by feature. `features` fixes the color order. */
export function StackedSpendChart({ series, features }: { series: SpendSeries; features?: string[] }) {
  const hourly = series.granularity === "hour";
  const order = useMemo(() => {
    const keys = Object.keys(series.by_feature ?? {});
    if (!features) return keys;
    const known = features.filter((f) => keys.includes(f));
    return [...known, ...keys.filter((k) => !known.includes(k))];
  }, [series, features]);

  return (
    <SpendStackedBars
      buckets={series.buckets ?? []}
      series={series.by_feature ?? {}}
      features={order}
      xLabel={(b) => fmtDateShort(b, hourly)}
      tooltipTitle={(b) =>
        hourly ? `${b.slice(0, 10)} ${fmtDateShort(b, true)} UTC` : `${b.slice(0, 10)} (${series.granularity})`
      }
      ariaLabel="Stacked spend by feature over time. Focus and use left and right arrow keys to read values."
    />
  );
}
