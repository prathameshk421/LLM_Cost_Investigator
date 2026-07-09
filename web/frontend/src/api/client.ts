import type { IncidentSummary, ReplayIncident } from "../types/replay";

const BASE = "";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export function listIncidents(): Promise<IncidentSummary[]> {
  return getJson("/api/incidents");
}

export function getIncident(id: string): Promise<ReplayIncident> {
  return getJson(`/api/incidents/${encodeURIComponent(id)}`);
}
