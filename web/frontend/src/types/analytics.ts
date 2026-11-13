import type { LiveIncident } from "./investigate";

export type RangeDays = 1 | 7 | 30 | 90;

export interface Kpis {
  spend_usd: number;
  calls: number;
  tokens: number;
  cost_per_1k_tokens: number;
  cost_per_call: number;
  spend_prev_period_usd: number;
  spend_change_pct: number | null;
  mtd_spend_usd: number;
  forecast_month_usd: number;
  forecast_p10_usd: number;
  forecast_p90_usd: number;
  burn_rate_usd_per_day: number;
  retry_waste_usd: number;
  p95_latency_ms: number;
}

export interface SpendSeries {
  granularity: "hour" | "day";
  buckets: string[];
  by_feature: Record<string, number[]>;
}

export interface BreakdownRow {
  key: string;
  spend_usd: number;
  calls: number;
  tokens: number;
  cost_per_1k_tokens: number;
  share: number;
  change_pct: number | null;
}

export interface Overview {
  as_of: string;
  days: number;
  kpis: Kpis;
  series: SpendSeries;
  by_feature: BreakdownRow[];
  by_model: BreakdownRow[];
}

export interface Driver {
  feature_tag: string;
  model: string;
  prev_usd: number;
  curr_usd: number;
  delta_usd: number;
  share_of_delta: number;
  volume_effect_usd: number;
  tokens_effect_usd: number;
  price_effect_usd: number;
}

export interface DriversResponse {
  as_of: string;
  days: number;
  total_delta_usd: number;
  drivers: Driver[];
}

export interface WhatIfRow {
  feature_tag: string;
  model: string;
  actual_usd: number;
  projected_usd: number;
  savings_usd: number;
}

export interface WhatIfResponse {
  to_model: string;
  days: number;
  actual_usd: number;
  projected_usd: number;
  savings_usd: number;
  savings_pct: number;
  monthly_savings_usd: number;
  rows: WhatIfRow[];
  pricing_source: string;
}

export interface WasteResponse {
  retry_waste_usd: number;
  retry_waste_by_feature: Array<{ feature_tag: string; usd: number; retried_calls: number }>;
  deep_chain_cost_usd: number;
  top_chains: Array<{ root_call_id: string; feature_tag: string; depth: number; calls: number; usd: number }>;
}

export type BudgetScope = "global" | "feature" | "model";
export type BudgetStatus = "ok" | "warning" | "over" | "forecast_over";

export interface Budget {
  budget_id: string;
  scope: BudgetScope;
  scope_value: string;
  monthly_usd: number;
  alert_pct: number;
  mtd_spend_usd: number;
  forecast_month_usd: number;
  pct_used: number;
  forecast_pct: number;
  status: BudgetStatus;
  days_left: number;
}

export interface NewBudget {
  scope: BudgetScope;
  scope_value: string;
  monthly_usd: number;
  alert_pct: number;
}

export interface WarehouseStats {
  calls: number;
  start: string | null;
  end: string | null;
  path: string;
  size_mb: number;
}

export interface PricingRow {
  model: string;
  input_per_million_usd: number;
  output_per_million_usd: number;
}

// ---- live ----
export interface StatsEvent {
  events_per_sec: number;
  queue_depth: number;
  total_calls: number;
  spend_last_5m_usd: number;
  ts: string;
}

export interface IngestEvent {
  count: number;
  spend_usd: number;
  by_feature: Record<string, number>;
  ts: string;
}

export interface LiveIncidentSummary {
  incident_id: string;
  feature_tag: string;
  start: string;
  end: string;
  hypothesis: string;
  confidence: number;
  cost_usd: number;
  status: string;
}

export interface BudgetEvent {
  budget_id: string;
  scope: BudgetScope;
  scope_value: string;
  pct_used: number;
  status: BudgetStatus;
}

export type DemoIncident = "model_misroute" | "retry_loop" | "context_bloat" | null;

export interface DemoState {
  running: boolean;
  rate: number;
  incident: string | null;
}

export type DiagnoseProvider = "hf" | "fallback";
export type DiagnosedIncident = LiveIncident;
