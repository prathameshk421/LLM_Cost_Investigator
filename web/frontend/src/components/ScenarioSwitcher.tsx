import type { IncidentSummary } from "../types/replay";

export function ScenarioSwitcher({
  items,
  selectedId,
  onSelect,
}: {
  items: IncidentSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const main = items.filter((i) => i.kind === "main");
  const thin = items.filter((i) => i.kind === "thin_must_call");

  return (
    <div>
      <Group title="Main scenarios" items={main} selectedId={selectedId} onSelect={onSelect} />
      <Group title="Thin MUST-CALL demos" items={thin} selectedId={selectedId} onSelect={onSelect} />
    </div>
  );
}

function Group({
  title,
  items,
  selectedId,
  onSelect,
}: {
  title: string;
  items: IncidentSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="scenario-group">
      <h3>{title}</h3>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={`scenario-btn ${selectedId === item.id ? "active" : ""}`}
          onClick={() => onSelect(item.id)}
        >
          <span className="title">{item.title}</span>
          <span className="meta">
            {item.feature_tag}
            {item.has_tool_use ? " · tool" : " · no tool"} · {item.root_cause_hypothesis}
          </span>
        </button>
      ))}
    </div>
  );
}
