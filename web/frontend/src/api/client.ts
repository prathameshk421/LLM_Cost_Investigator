import type { IncidentSummary, ReplayIncident } from "../types/replay";
import type {
  AdapterChoice,
  InvestigateResponse,
  PreviewResponse,
} from "../types/investigate";
import type {
  ConnectorInfo,
  ConnectorTestResponse,
  ProfileSummary,
  RemotePullParams,
} from "../types/connectors";

const BASE = "";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = (await res.json()) as { detail?: string };
      if (data.detail) detail = data.detail;
    } catch {
      const text = await res.text().catch(() => "");
      if (text) detail = text;
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function listIncidents(): Promise<IncidentSummary[]> {
  return getJson("/api/incidents");
}

export function getIncident(id: string): Promise<ReplayIncident> {
  return getJson(`/api/incidents/${encodeURIComponent(id)}`);
}

export interface InvestigateParams {
  file: File;
  adapter: AdapterChoice;
  featureDefault?: string;
  columnMapping?: Record<string, string> | string;
  label?: string;
  provider?: string;
  model?: string;
  forceFallback?: boolean;
  maxAnomalies?: number;
}

function appendMapping(form: FormData, mapping?: Record<string, string> | string) {
  if (!mapping) return;
  if (typeof mapping === "string") {
    form.append("column_mapping", mapping);
  } else {
    form.append("column_mapping", JSON.stringify(mapping));
  }
}

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      const text = await res.text().catch(() => "");
      if (text) detail = text;
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function previewIngest(params: InvestigateParams): Promise<PreviewResponse> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("adapter", params.adapter);
  if (params.featureDefault) form.append("feature_default", params.featureDefault);
  appendMapping(form, params.columnMapping);
  return postForm("/api/investigate/preview", form);
}

export function runInvestigate(params: InvestigateParams): Promise<InvestigateResponse> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("adapter", params.adapter);
  if (params.featureDefault) form.append("feature_default", params.featureDefault);
  appendMapping(form, params.columnMapping);
  if (params.label) form.append("label", params.label);
  if (params.provider) form.append("provider", params.provider);
  if (params.model) form.append("model", params.model);
  form.append("force_fallback", params.forceFallback === false ? "false" : "true");
  form.append("max_anomalies", String(params.maxAnomalies ?? 5));
  return postForm("/api/investigate", form);
}

export function listConnectors(): Promise<ConnectorInfo[]> {
  return getJson("/api/connectors");
}

export function listConnectorProfiles(): Promise<ProfileSummary[]> {
  return getJson("/api/connectors/profiles");
}

export function testConnector(params: RemotePullParams): Promise<ConnectorTestResponse> {
  return postJson("/api/connectors/test", params);
}

export function previewConnector(params: RemotePullParams): Promise<PreviewResponse> {
  return postJson("/api/connectors/preview", params);
}

export function runConnectorInvestigate(
  params: RemotePullParams
): Promise<InvestigateResponse> {
  return postJson("/api/connectors/investigate", params);
}
