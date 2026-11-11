import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getBudgets,
  getDrivers,
  getOverview,
  getPricing,
  getWarehouseStats,
  getWaste,
  seedWarehouse,
} from "../api/analytics";
import type { Budget, DriversResponse, Overview, PricingRow, WasteResponse } from "../types/analytics";
import { SpendBudgets } from "./SpendBudgets";
import { SpendDrivers } from "./SpendDrivers";
import { errMsg, fmtMs, fmtNum, fmtPct, fmtUsd } from "./SpendFormat";
import { SpendWaste } from "./SpendWaste";
import { SpendWhatIf } from "./SpendWhatIf";
import { StackedSpendChart } from "./StackedSpendChart";

const RANGES = [
  { days: 1, label: "24h" },
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
] as const;

interface Loaded {
  overview: Overview;
  drivers: DriversResponse | null;
  waste: WasteResponse | null;
  budgets: Budget[];
  pricing: PricingRow[];
  errors: { drivers: string | null; waste: string | null; budgets: string | null; pricing: string | null };
}

function settle<T>(r: PromiseSettledResult<T>): { value: T | null; error: string | null } {
  return r.status === "fulfilled" ? { value: r.value, error: null } : { value: null, error: errMsg(r.reason) };
}

function Tile({
  label,
  value,
  sub,
  children,
  wide,
}: {
  label: string;
  value: string;
  sub?: string;
  children?: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className={"sp-tile" + (wide ? " sp-tile-wide" : "")}>
      <div className="label">{label}</div>
      <div className="sp-tile-value mono">{value}</div>
      {children}
      {sub ? <div className="sp-dim">{sub}</div> : null}
    </div>
  );
}

function Change({ pct }: { pct: number | null }) {
  if (pct === null || !Number.isFinite(pct)) {
    return (
      <div className="sp-change sp-change-flat">
        n/a <span className="sp-dim">no data in the previous period</span>
      </div>
    );
  }
  const v = pct;
  const dir = v > 0.05 ? "up" : v < -0.05 ? "down" : "flat";
  const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "▶";
  const sign = v > 0 ? "+" : "";
  return (
    <div className={`sp-change sp-change-${dir}`}>
      <span aria-hidden="true">{arrow}</span> {sign}
      {v.toFixed(1)}% <span className="sp-dim">vs previous period</span>
    </div>
  );
}

export function SpendView() {
  const [days, setDays] = useState<number>(30);
  const [empty, setEmpty] = useState(false);
  const [data, setData] = useState<Loaded | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const seq = useRef(0);

  const load = useCallback(async (range: number) => {
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const stats = await getWarehouseStats();
      if (id !== seq.current) return;
      if (!stats.calls) {
        setEmpty(true);
        setData(null);
        return;
      }
      setEmpty(false);
      const [ov, dr, wa, bu, pr] = await Promise.allSettled([
        getOverview(range),
        getDrivers(range),
        getWaste(range),
        getBudgets(),
        getPricing(),
      ]);
      if (id !== seq.current) return;
      if (ov.status === "rejected") throw ov.reason;
      const d = settle(dr);
      const w = settle(wa);
      const b = settle(bu);
      const p = settle(pr);
      setData({
        overview: ov.value,
        drivers: d.value,
        waste: w.value,
        budgets: b.value ?? [],
        pricing: p.value ?? [],
        errors: { drivers: d.error, waste: w.error, budgets: b.error, pricing: p.error },
      });
    } catch (e) {
      if (id === seq.current) setError(errMsg(e));
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(days);
  }, [days, load]);

  async function seed() {
    setSeeding(true);
    setError(null);
    try {
      await seedWarehouse(60);
      await load(days);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSeeding(false);
    }
  }

  const featureKeys = useMemo(() => (data?.overview.by_feature ?? []).map((r) => r.key), [data]);
  const modelKeys = useMemo(() => (data?.overview.by_model ?? []).map((r) => r.key), [data]);

  const header = (
    <div className="sp-head">
      <div>
        <h2 className="sp-title">Spend</h2>
        <div className="sp-dim">
          {data ? `As of ${data.overview.as_of.replace("T", " ").slice(0, 16)} UTC` : "LLM spend, forecast and budgets"}
        </div>
      </div>
      <div className="sp-range" role="group" aria-label="Time range">
        {RANGES.map((r) => (
          <button
            key={r.days}
            type="button"
            className={"btn" + (days === r.days ? " btn-active btn-primary" : "")}
            aria-pressed={days === r.days}
            onClick={() => setDays(r.days)}
          >
            {r.label}
          </button>
        ))}
      </div>
    </div>
  );

  if (empty) {
    return (
      <div className="sp-root">
        {header}
        <div className="sp-card sp-empty" role="status">
          <h3 className="sp-card-title">No data yet</h3>
          <p>
            The warehouse has 0 calls. Send events to <span className="mono">POST /v1/ingest</span>, upload a file in
            Investigate, or load demo history to explore the dashboard.
          </p>
          {error ? <div className="error-banner">{error}</div> : null}
          <button type="button" className="btn btn-primary" onClick={seed} disabled={seeding}>
            {seeding ? "Loading demo history..." : "Load 60 days of demo history"}
          </button>
          <p className="sp-dim">This replaces any data in the warehouse. It adds three injected incidents.</p>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="sp-root">
        {header}
        <div className="error-banner" role="alert">
          {error}
        </div>
        <button type="button" className="btn" onClick={() => load(days)}>
          Retry
        </button>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="sp-root">
        {header}
        <div className="sp-card sp-empty-inline" role="status">Loading spend data...</div>
      </div>
    );
  }

  const k = data.overview.kpis;
  const rangeLabel = days === 1 ? "last 24 h" : `last ${days} d`;
  return (
    <div className={"sp-root" + (loading ? " sp-busy" : "")}>
      {header}
      {error ? <div className="error-banner" role="alert">{error}</div> : null}

      <div className="sp-tiles">
        <Tile label={`Spend (${rangeLabel})`} value={fmtUsd(k.spend_usd)} wide>
          <Change pct={k.spend_change_pct} />
        </Tile>
        <Tile label="Calls" value={fmtNum(k.calls)} sub={`${fmtNum(k.tokens)} tokens`} />
        <Tile label="Cost per 1k tokens" value={fmtUsd(k.cost_per_1k_tokens)} />
        <Tile label="Cost per call" value={fmtUsd(k.cost_per_call)} />
        <Tile
          label="Month to date"
          value={fmtUsd(k.mtd_spend_usd)}
          sub={`Forecast ${fmtUsd(k.forecast_month_usd)} (P10 ${fmtUsd(k.forecast_p10_usd)} to P90 ${fmtUsd(k.forecast_p90_usd)})`}
          wide
        />
        <Tile label="Burn rate" value={`${fmtUsd(k.burn_rate_usd_per_day)}/day`} />
        <Tile
          label="Retry waste"
          value={fmtUsd(k.retry_waste_usd)}
          sub={k.spend_usd > 0 ? `${fmtPct((k.retry_waste_usd / k.spend_usd) * 100, 1)} of spend` : undefined}
        />
        <Tile label="p95 latency" value={fmtMs(k.p95_latency_ms)} />
      </div>

      <section className="sp-card" aria-labelledby="sp-chart-h">
        <h3 className="sp-card-title" id="sp-chart-h">
          Spend by feature per {data.overview.series.granularity}
        </h3>
        <StackedSpendChart series={data.overview.series} features={featureKeys} />
      </section>

      <div className="sp-grid2">
        <SpendBudgets
          budgets={data.budgets}
          error={data.errors.budgets}
          featureKeys={featureKeys}
          modelKeys={modelKeys}
          onChanged={() => void load(days)}
        />
        <SpendWhatIf pricing={data.pricing} pricingError={data.errors.pricing} featureKeys={featureKeys} days={days} />
      </div>

      <SpendDrivers data={data.drivers} error={data.errors.drivers} days={days} />

      <div className="sp-grid2">
        <SpendWaste data={data.waste} error={data.errors.waste} />
        <section className="sp-card" aria-labelledby="sp-models-h">
          <h3 className="sp-card-title" id="sp-models-h">Spend by model</h3>
          {data.overview.by_model.length === 0 ? (
            <div className="sp-empty-inline">No model data.</div>
          ) : (
            <div className="sp-scroll">
              <table className="sp-table sp-table-compact">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th className="sp-num">Spend</th>
                    <th className="sp-num">Share</th>
                    <th className="sp-num">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {data.overview.by_model.map((m) => (
                    <tr key={m.key}>
                      <td className="mono">{m.key}</td>
                      <td className="sp-num mono">{fmtUsd(m.spend_usd)}</td>
                      <td className="sp-num mono">{fmtPct(m.share * 100)}</td>
                      <td className="sp-num mono">
                        {m.change_pct === null ? "new" : (
                          <>
                            {m.change_pct > 0 ? "▲ +" : m.change_pct < 0 ? "▼ " : ""}
                            {m.change_pct.toFixed(1)}%
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
