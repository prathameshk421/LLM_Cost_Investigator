const LOGICAL_FIELDS: Array<{ key: string; label: string; required: boolean }> = [
  { key: "timestamp", label: "timestamp", required: true },
  { key: "call_id", label: "call_id", required: true },
  { key: "feature_tag", label: "feature_tag", required: true },
  { key: "model", label: "model", required: true },
  { key: "input_tokens", label: "input_tokens", required: true },
  { key: "output_tokens", label: "output_tokens", required: true },
  { key: "cost_usd", label: "cost_usd", required: false },
  { key: "latency_ms", label: "latency_ms", required: false },
  { key: "retry_count", label: "retry_count", required: false },
  { key: "parent_call_id", label: "parent_call_id", required: false },
];

export function CsvMappingForm({
  headers,
  mapping,
  onChange,
}: {
  headers: string[];
  mapping: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  return (
    <div className="csv-mapping">
      <div className="label" style={{ marginBottom: 8 }}>
        CSV column mapping
      </div>
      <div className="csv-mapping-grid">
        {LOGICAL_FIELDS.map((field) => (
          <label key={field.key} className="csv-mapping-row">
            <span>
              {field.label}
              {field.required ? " *" : ""}
            </span>
            <select
              value={mapping[field.key] ?? ""}
              onChange={(e) => {
                const next = { ...mapping };
                if (e.target.value) next[field.key] = e.target.value;
                else delete next[field.key];
                onChange(next);
              }}
            >
              <option value="">—</option>
              {headers.map((h) => (
                <option key={h} value={h}>
                  {h}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </div>
  );
}
