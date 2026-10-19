import { useEffect, useRef, useState } from 'react';

/**
 * Rolling frames-per-second, for proving the slider is actually smooth rather
 * than asserting it. Only mounted when the page is opened with `?fps=1`, so it
 * costs nothing in normal use.
 *
 * Background tabs are throttled by the browser, so a window longer than two
 * seconds is discarded instead of being reported as a stall.
 */
export function useFrameRate(enabled: boolean): { fps: number; low: number } {
  const [reading, setReading] = useState({ fps: 0, low: 0 });
  const lowest = useRef(Number.POSITIVE_INFINITY);

  useEffect(() => {
    if (!enabled) return;
    let frames = 0;
    let windowStart = performance.now();
    let raf = 0;

    const tick = () => {
      frames += 1;
      const now = performance.now();
      const elapsed = (now - windowStart) / 1000;
      if (elapsed >= 0.5) {
        if (elapsed <= 2 && document.visibilityState === 'visible') {
          const fps = frames / elapsed;
          lowest.current = Math.min(lowest.current, fps);
          setReading({ fps: Math.round(fps), low: Math.round(lowest.current) });
        }
        frames = 0;
        windowStart = now;
      }
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [enabled]);

  return reading;
}
