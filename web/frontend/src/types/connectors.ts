export interface ConnectorInfo {
  name: string;
  description: string;
  required_env: string[];
  optional_env: string[];
}

export interface ProfileSummary {
  name: string;
  connector: string;
  host?: string | null;
  api_version: string;
  defaults: Record<string, unknown>;
  filters: Record<string, unknown>;
}

export interface RemotePullParams {
  connection?: string;
  connector?: string;
  since_hours?: number;
  max_rows?: number;
  feature_default?: string;
  filters?: Record<string, string>;
  label?: string;
  provider?: string;
  model?: string;
  force_fallback?: boolean;
  max_anomalies?: number;
}

export interface ConnectorTestResponse {
  ok: boolean;
  message: string;
  accepted: number;
  dropped: number;
  pull_meta?: Record<string, unknown> | null;
}
