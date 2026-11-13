export function ReplayControls({
  atStart,
  atEnd,
  auto,
  onPrev,
  onNext,
  onReset,
  onToggleAuto,
}: {
  atStart: boolean;
  atEnd: boolean;
  auto: boolean;
  onPrev: () => void;
  onNext: () => void;
  onReset: () => void;
  onToggleAuto: () => void;
}) {
  return (
    <div className="controls-bar">
      <button type="button" className="btn" onClick={onPrev} disabled={atStart}>
        Prev
      </button>
      <button type="button" className="btn btn-primary" onClick={onNext} disabled={atEnd}>
        Next
      </button>
      <button
        type="button"
        className={`btn ${auto ? "btn-active" : ""}`}
        onClick={onToggleAuto}
      >
        {auto ? "Auto on" : "Auto"}
      </button>
      <button type="button" className="btn" onClick={onReset}>
        Reset
      </button>
    </div>
  );
}
