import { memo, useMemo, useState } from 'react';

import type { Route } from '../types';
import { RISK_COLORS, RISK_TEXT_ON, formatClock } from '../risk';

/*
 * Every checkpoint on the selected route: mile mark, the arrival time used to
 * look the forecast up, the readings, and the verdict.
 *
 * A 1,000 mile route sampled every 10 miles is a hundred rows, so there is a
 * filter for the ones that are not Low. The full list stays available, because
 * "nothing on this route is above Low" is itself the answer a driver wants.
 */

interface Props {
  route: Route;
  intervalMiles: number;
}

/* Memoised. The forecast hour lives in App state, so without this every slider
   tick re-renders this whole subtree - measured at 14 fps while dragging, with
   the checkpoint table alone reconciling a few hundred elements per frame. Its
   props do not depend on the hour, so React.memo skips it entirely and the
   slider only re-renders what actually changes. */
export const CheckpointList = memo(function CheckpointList({ route, intervalMiles }: Props) {
  const [riskyOnly, setRiskyOnly] = useState(false);

  const risky = useMemo(
    () => route.checkpoints.filter((point) => point.risk_level > 0).length,
    [route],
  );
  const rows = riskyOnly
    ? route.checkpoints.filter((point) => point.risk_level > 0)
    : route.checkpoints;

  return (
    <section className="checkpoints">
      <header className="cp-header">
        <h2>Checkpoints</h2>
        <span className="muted">
          every {intervalMiles} mi &middot; {route.checkpoints.length} points
        </span>
      </header>

      <label className="toggle small">
        <input
          type="checkbox"
          checked={riskyOnly}
          onChange={(e) => setRiskyOnly(e.target.checked)}
          disabled={risky === 0}
        />
        <span>
          {risky === 0
            ? 'Every checkpoint on this route is Low'
            : `Show only the ${risky} above Low`}
        </span>
      </label>

      <div className="cp-scroll">
        <table className="cp-table">
          <thead>
            <tr>
              <th scope="col">Mile</th>
              <th scope="col">ETA</th>
              <th scope="col">Wind</th>
              <th scope="col">Rain</th>
              <th scope="col">Snow</th>
              <th scope="col">Risk</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((point) => (
              <tr key={point.index} className={point.risk_level > 1 ? 'warn' : undefined}>
                <td className="num">{Math.round(point.miles)}</td>
                <td className="when">{formatClock(point.eta)}</td>
                <td className="num">{fmt(point.forecast.wind_mph)}</td>
                <td className="num">{fmt(point.forecast.rain_in_hr, 2)}</td>
                <td className="num">{fmt(point.forecast.snow_in_hr, 2)}</td>
                <td>
                  <span
                    className="pill"
                    style={{ background: RISK_COLORS[point.risk], color: RISK_TEXT_ON[point.risk] }}
                    title={point.reason}
                  >
                    {point.risk}
                    {point.load_escalated && <b aria-label="raised by load weight"> ↑</b>}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="hint">
        <b>↑</b> marks a checkpoint the load weight pushed into a worse band.
        ETA is the arrival time this checkpoint's forecast was read at.
      </p>
    </section>
  );
});

function fmt(value: number | null, places = 0): string {
  if (value === null || value === undefined) return '–';
  return value.toFixed(places);
}
