import { useEffect, useState } from "react";

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export function CountUp({
  value,
  duration = 600,
  decimals,
}: {
  value: number;
  duration?: number;
  decimals?: number;
}) {
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setDisplay(value);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const from = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(from + (value - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);

  const places =
    decimals ?? (Number.isInteger(value) ? 0 : Math.min(2, (String(value).split(".")[1] || "").length));
  return <>{display.toFixed(places)}</>;
}
