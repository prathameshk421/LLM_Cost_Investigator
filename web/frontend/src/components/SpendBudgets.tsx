import { useState } from "react";
import { createBudget, deleteBudget } from "../api/analytics";
import type { Budget, BudgetScope, BudgetStatus } from "../types/analytics";
import { errMsg, fmtPct, fmtUsd } from "./SpendFormat";

export const STATUS_META: Record<BudgetStatus, { icon: string; text: string; cls: string }> = {
  ok: { icon: "✓", text: "On track", cls: "ok" },
  warning: { icon: "⚠", text: "Warning", cls: "warn" },
  over: { icon: "✕", text: "Over budget", cls: "bad" },
  forecast_over: { icon: "↗", text: "Forecast over", cls: "warn" },
};

export function StatusPill({ status }: { status: BudgetStatus }) {
  const m = STATUS_META[status] ?? STATUS_META.ok;
  return (
    <span className={`sp-pill sp-pill-${m.cls}`}>
      <span aria-hidden="true">{m.icon}</span> {m.text}
    </span>
  );
}

function BudgetRow({ b, onDelete, busy }: { b: Budget; onDelete: () => void; busy: boolean }) {
  // API sends ratios (1.0 = 100 %); the UI shows percents.
  const usedPct = b.pct_used * 100;
  const fcPct = b.forecast_pct * 100;
  const alertPct = b.alert_pct * 100;
  const used = Math.max(0, Math.min(100, usedPct));
  const fc = Math.max(0, Math.min(100, fcPct));
  const name = b.scope === "global" ? "All spend" : `${b.scope}: ${b.scope_value}`;
  return (
    <li className="sp-budget">
      <div className="sp-budget-head">
        <span className="sp-budget-name">{name}</span>
        <StatusPill status={b.status} />
        <button type="button" className="btn sp-btn-sm" onClick={onDelete} disabled={busy} aria-label={`Delete budget ${name}`}>
          Delete
        </button>
      </div>
      <div
        className="sp-bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(used)}
        aria-label={`${name} month to date, ${fmtPct(usedPct)} used, forecast ${fmtPct(fcPct)}`}
      >
        <span className={`sp-bar-fill sp-fill-${b.status}`} style={{ width: `${used}%` }} />
        <span className="sp-bar-alert" style={{ left: `${Math.min(100, alertPct)}%` }} title={`Alert at ${alertPct.toFixed(0)}%`} />
        <span className="sp-bar-forecast" style={{ left: `calc(${fc}% - 1px)` }} title={`Forecast ${fmtPct(fcPct)}`} />
      </div>
      <div className="sp-budget-meta mono">
        {fmtUsd(b.mtd_spend_usd)} of {fmtUsd(b.monthly_usd)} ({fmtPct(usedPct)}) · forecast {fmtUsd(b.forecast_month_usd)} (
        {fmtPct(fcPct)}) · alert {alertPct.toFixed(0)}% · {Math.ceil(b.days_left)} d left
      </div>
    </li>
  );
}

export function SpendBudgets({
  budgets,
  error,
  featureKeys,
  modelKeys,
  onChanged,
}: {
  budgets: Budget[];
  error: string | null;
  featureKeys: string[];
  modelKeys: string[];
  onChanged: () => void;
}) {
  const [scope, setScope] = useState<BudgetScope>("global");
  const [value, setValue] = useState("");
  const [monthly, setMonthly] = useState("1000");
  const [alertPct, setAlertPct] = useState("80");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const options = scope === "feature" ? featureKeys : scope === "model" ? modelKeys : [];
  const chosen = scope === "global" ? "" : options.includes(value) ? value : options[0] ?? "";
  const monthlyNum = Number(monthly);
  const alertNum = Number(alertPct);
  const valid = monthlyNum > 0 && alertNum > 0 && alertNum <= 100 && (scope === "global" || !!chosen);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    setErr(null);
    try {
      await createBudget({ scope, scope_value: scope === "global" ? "*" : chosen, monthly_usd: monthlyNum, alert_pct: alertNum / 100 });
      onChanged();
    } catch (x) {
      setErr(errMsg(x));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    setErr(null);
    try {
      await deleteBudget(id);
      onChanged();
    } catch (x) {
      setErr(errMsg(x));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sp-card" aria-labelledby="sp-budgets-h">
      <h3 className="sp-card-title" id="sp-budgets-h">Budgets (month to date)</h3>
      <p className="sp-help">
        Filled bar is spend so far. The white tick is the forecast for month end. The dashed tick is the alert level.
      </p>
      {error ? <div className="error-banner">Budgets failed to load: {error}</div> : null}
      {budgets.length === 0 && !error ? <div className="sp-empty-inline">No budgets yet. Add one below.</div> : null}
      <ul className="sp-budget-list">
        {budgets.map((b) => (
          <BudgetRow key={b.budget_id} b={b} busy={busy} onDelete={() => remove(b.budget_id)} />
        ))}
      </ul>

      <form className="sp-form" onSubmit={add}>
        <label>
          Scope
          <select value={scope} onChange={(e) => setScope(e.target.value as BudgetScope)}>
            <option value="global">global</option>
            <option value="feature">feature</option>
            <option value="model">model</option>
          </select>
        </label>
        <label>
          Value
          <select value={chosen} disabled={scope === "global" || options.length === 0} onChange={(e) => setValue(e.target.value)}>
            {scope === "global" ? <option value="">all spend</option> : null}
            {options.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        </label>
        <label>
          Monthly USD
          <input type="number" min={1} step="any" value={monthly} onChange={(e) => setMonthly(e.target.value)} />
        </label>
        <label>
          Alert at %
          <input type="number" min={1} max={100} value={alertPct} onChange={(e) => setAlertPct(e.target.value)} />
        </label>
        <button type="submit" className="btn btn-primary" disabled={!valid || busy}>
          Add budget
        </button>
      </form>
      {err ? <div className="error-banner" style={{ marginTop: 10 }}>{err}</div> : null}
    </section>
  );
}
