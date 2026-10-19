import { memo, useCallback, useEffect, useMemo, useRef } from 'react';

import type { Heatmap, RiskLabel } from '../types';
import { RISK_COLORS, formatHourLabel } from '../risk';

/*
 * The forecast slider and the risk legend.
 *
 * These began as panels floating over the map, and they covered the corner the
 * route usually ends in. Shrinking them and letting them collapse both helped
 * and both still left the map partly hidden, so they moved into the sidebar
 * instead. The map now carries nothing except its own controls, which is the
 * only version where the whole route is always visible.
 *
 * Dragging the slider transfers nothing and recomputes nothing: every corridor
 * point already carries a weight for all 48 hours, so the map only has to be
 * told which hour to show (see RouteMap for how it does that).
 *
 * While the pointer is down the hour does not go through React state. It calls
 * `onLive`, which reaches the map directly, and the two things that have to
 * follow the pointer - the time label and the marker over the bar strip - are
 * written straight to the DOM. On release `onCommit` puts the final value into
 * state. Measured on the target machine, that difference was 28 fps against 34.
 */

const RISK_BANDS: RiskLabel[] = ['Low', 'Moderate', 'High', 'Severe', 'No Travel'];

interface Props {
  heatmap: Heatmap;
  /** Committed hour. Also the value the input resets to when a plan loads. */
  hour: number;
  /** Called continuously while dragging. Must be cheap and must not setState. */
  onLive: (hour: number) => void;
  /** Called once when the drag ends. */
  onCommit: (hour: number) => void;
  visible: boolean;
  onVisible: (visible: boolean) => void;
}

export const HeatmapSlider = memo(function HeatmapSlider({
  heatmap, hour, onLive, onCommit, visible, onVisible,
}: Props) {
  const input = useRef<HTMLInputElement>(null);
  const label = useRef<HTMLElement>(null);
  const offset = useRef<HTMLElement>(null);
  const cursor = useRef<HTMLElement>(null);
  const peakOut = useRef<HTMLElement>(null);

  // Depends only on the plan, so dragging never recomputes it.
  const peaks = useMemo(() => {
    const out: number[] = [];
    for (let h = 0; h < heatmap.hours; h += 1) {
      let worst = 0;
      for (const point of heatmap.points) worst = Math.max(worst, point.h[h] ?? 0);
      out.push(worst);
    }
    return out;
  }, [heatmap]);

  const span = Math.max(1, heatmap.hours - 1);

  /** Write the parts that have to follow the pointer, without a render. */
  const paint = useCallback((value: number) => {
    const time = heatmap.times[value];
    if (label.current) label.current.textContent = time ? formatHourLabel(time) : `+${value} h`;
    if (offset.current) offset.current.textContent = value === 0 ? 'at departure' : `departure + ${value} h`;
    if (cursor.current) cursor.current.style.left = `${(value / span) * 100}%`;
    if (peakOut.current) {
      peakOut.current.textContent =
        `worst in corridor: ${((peaks[value] ?? 0) * 100).toFixed(0)}% of the no-travel threshold`;
    }
  }, [heatmap, peaks, span]);

  // A new plan, or an hour set from outside, resets the uncontrolled input.
  useEffect(() => {
    if (input.current) input.current.value = String(hour);
    paint(hour);
  }, [hour, paint]);

  return (
    <section className="slider-panel">
      <h2>Forecast hour</h2>
      <div className="slider-head">
        <label className="toggle">
          <input type="checkbox" checked={visible} onChange={(e) => onVisible(e.target.checked)} />
          <span>Weather heatmap</span>
        </label>

        <div className="slider-when">
          <strong ref={label as React.RefObject<HTMLElement>} />
          <span className="muted" ref={offset as React.RefObject<HTMLElement>} />
        </div>

      </div>

      <div className="slider-body">
        <div className="spark-wrap">
          <Spark peaks={peaks} />
          <i className="spark-cursor" ref={cursor as React.RefObject<HTMLElement>} aria-hidden />
        </div>

        <input
          ref={input}
          className="range"
          type="range"
          min={0}
          max={span}
          step={1}
          defaultValue={hour}
          disabled={!visible}
          onInput={(e) => {
            const value = Number(e.currentTarget.value);
            paint(value);
            onLive(value);
          }}
          onPointerUp={(e) => onCommit(Number(e.currentTarget.value))}
          onKeyUp={(e) => onCommit(Number(e.currentTarget.value))}
          onBlur={(e) => onCommit(Number(e.currentTarget.value))}
          aria-label="Forecast hour"
        />

        <div className="slider-foot">
          <span>now</span>
          <span className="muted" ref={peakOut as React.RefObject<HTMLElement>} />
          <span>+{span} h</span>
        </div>

        <ul className="legend-row">
          {RISK_BANDS.map((band) => (
            <li key={band}>
              <i style={{ background: RISK_COLORS[band] }} aria-hidden />
              {band}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
});

/** The 48 severity bars. Memoised on `peaks`, so a drag never re-renders them. */
const Spark = memo(function Spark({ peaks }: { peaks: number[] }) {
  return (
    <div className="spark" role="img" aria-label="Worst corridor severity per forecast hour">
      {peaks.map((value, index) => (
        <i
          key={index}
          style={{ height: `${Math.max(2, value * 100)}%` }}
          title={`+${index} h: ${(value * 100).toFixed(0)}% of the no-travel threshold`}
        />
      ))}
    </div>
  );
});
