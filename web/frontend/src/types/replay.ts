export type IncidentKind = "main" | "thin_must_call";
export type StageId =
  | "signals"
  | "router"
  | "decision"
  | "tool_trace"
  | "explanation"
  | "root_cause";
export type StageKind = "deterministic" | "agentic";

export interface IncidentSummary {
  id: string;
  title: string;
  kind: IncidentKind;
  feature_tag: string;
  root_cause_hypothesis: string;
  has_tool_use: boolean;
  winning_agent: string | null;
}

export interface Citation {
  value: string | number;
  display: string;
  explanation_span: { start: number; end: number };
  source: {
    tool_name: string;
    path: string;
    field: string;
    raw: unknown;
  };
  match_kind: string;
}

export interface ReplayStage {
  id: StageId;
  title: string;
  kind: StageKind;
  agent_name?: string | null;
  payload: Record<string, unknown>;
}

export interface ReplayIncident {
  id: string;
  title: string;
  kind: IncidentKind;
  scenario_label: string;
  feature_tag: string;
  window: { start_time: string; end_time: string };
  stages: ReplayStage[];
  root_cause: {
    hypothesis: string;
    confidence: number;
    winning_agent: string | null;
  };
  recommendations: string[];
  meta: {
    provider?: string | null;
    model?: string | null;
    generated_at?: string | null;
    source_files: string[];
  };
}
