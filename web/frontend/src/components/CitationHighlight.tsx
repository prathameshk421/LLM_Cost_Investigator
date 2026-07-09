import type { ReactNode } from "react";
import type { Citation } from "../types/replay";

export function CitationHighlight({
  text,
  citations,
}: {
  text: string;
  citations: Citation[];
}) {
  if (!citations.length) {
    return <p className="narrative">{text}</p>;
  }

  const sorted = [...citations].sort(
    (a, b) => a.explanation_span.start - b.explanation_span.start,
  );
  const parts: ReactNode[] = [];
  let cursor = 0;

  sorted.forEach((c, i) => {
    const { start, end } = c.explanation_span;
    if (start < cursor || end > text.length || start >= end) return;
    if (start > cursor) {
      parts.push(<span key={`t-${i}`}>{text.slice(cursor, start)}</span>);
    }
    parts.push(
      <mark
        key={`c-${i}`}
        className="cite-text"
        title={`${c.source.tool_name} ${c.source.path}`}
      >
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  if (cursor < text.length) {
    parts.push(<span key="tail">{text.slice(cursor)}</span>);
  }

  return <p className="narrative">{parts}</p>;
}
