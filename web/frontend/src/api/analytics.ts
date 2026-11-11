import type {
  Budget,
  DemoIncident,
  DemoState,
  DiagnoseProvider,
  DiagnosedIncident,
  DriversResponse,
  LiveIncidentSummary,
  NewBudget,
  Overview,
  PricingRow,
  WarehouseStats,
  WasteResponse,
  WhatIfResponse,
} from "../types/analytics";

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string };
        return `${(item.loc ?? []).slice(1).join(".")}: ${item.msg ?? ""}`;
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

async function fail(res: Response): Promise<never> {
  let detail = `${res.status} ${res.statusText}`;
  const text = await res.text().catch(() => "");
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (body.detail) detail = detailText(body.detail);
  } catch {
    if (text) detail = `${detail}: ${text}`;
  }
  throw new Error(detail);
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) await fail(res);
  return res.json() as Promise<T>;
}

async function sendJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) await fail(res);
  return res.json() as Promise<T>;
}

export const getOverview = (days: number) => getJson<Overview>(`/api/analytics/overview?days=${days}`);
export const getDrivers = (days: number) => getJson<DriversResponse>(`/api/analytics/drivers?days=${days}`);
export const getWaste = (days: number) => getJson<WasteResponse>(`/api/analytics/waste?days=${days}`);

export function getWhatIf(days: number, toModel: string, featureTag?: string): Promise<WhatIfResponse> {
  const q = new URLSearchParams({ days: String(days), to_model: toModel });
  if (featureTag) q.set("feature_tag", featureTag);
  return getJson(`/api/analytics/whatif?${q}`);
}

export const getBudgets = () => getJson<Budget[]>("/api/budgets");
export const createBudget = (b: NewBudget) => sendJson<Budget>("POST", "/api/budgets", b);
export const deleteBudget = (id: string) =>
  sendJson<{ deleted: boolean }>("DELETE", `/api/budgets/${encodeURIComponent(id)}`);

export const getWarehouseStats = () => getJson<WarehouseStats>("/api/warehouse/stats");
export const seedWarehouse = (days = 30) =>
  sendJson<{ inserted: number; start: string; end: string }>("POST", "/api/warehouse/seed", {
    days,
    seed: 1,
    incidents: true,
    reset: true,
  });

export const getPricing = () => getJson<PricingRow[]>("/api/investigate/pricing");

export const getLiveIncidents = (limit = 50) =>
  getJson<LiveIncidentSummary[]>(`/api/incidents/live?limit=${limit}`);
export const diagnoseIncident = (id: string, provider: DiagnoseProvider) =>
  sendJson<DiagnosedIncident>("POST", `/api/incidents/live/${encodeURIComponent(id)}/diagnose`, { provider });

export const setDemo = (action: "start" | "stop", rate: number, incident: DemoIncident) =>
  sendJson<DemoState>("POST", "/api/stream/demo", { action, rate, incident });
