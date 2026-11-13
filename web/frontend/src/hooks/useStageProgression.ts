import { useCallback, useEffect, useState } from "react";

export function useStageProgression(stageCount: number, incidentId: string | null) {
  const [index, setIndex] = useState(0);
  const [maxReached, setMaxReached] = useState(0);
  const [auto, setAuto] = useState(false);

  useEffect(() => {
    setIndex(0);
    setMaxReached(0);
    setAuto(false);
  }, [incidentId]);

  useEffect(() => {
    setMaxReached((m) => Math.max(m, index));
  }, [index]);

  const next = useCallback(() => {
    setIndex((i) => Math.min(stageCount - 1, i + 1));
  }, [stageCount]);

  const prev = useCallback(() => {
    setIndex((i) => Math.max(0, i - 1));
  }, []);

  const reset = useCallback(() => {
    setIndex(0);
    setMaxReached(0);
  }, []);

  const jump = useCallback(
    (i: number) => {
      if (i < 0 || i >= stageCount) return;
      if (i <= maxReached || i <= index) setIndex(i);
    },
    [stageCount, maxReached, index],
  );

  useEffect(() => {
    if (!auto || stageCount === 0) return;
    if (index >= stageCount - 1) {
      setAuto(false);
      return;
    }
    const delay = 3000;
    const t = window.setTimeout(() => next(), delay);
    return () => window.clearTimeout(t);
  }, [auto, index, stageCount, next]);

  return {
    index,
    maxReached,
    auto,
    setAuto,
    next,
    prev,
    reset,
    jump,
    atStart: index <= 0,
    atEnd: index >= stageCount - 1 || stageCount === 0,
  };
}
