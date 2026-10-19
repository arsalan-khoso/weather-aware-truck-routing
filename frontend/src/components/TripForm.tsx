import { memo, useState } from 'react';

import type { PlanRequest } from '../types';

/*
 * The trip inputs. Kept to exactly what the risk rules need: where, when, and
 * how heavy. The sampling interval is here too because it changes what gets
 * sampled, so it belongs with the request rather than with the map controls.
 */

interface Props {
  intervals: number[];
  busy: boolean;
  onSubmit: (request: PlanRequest) => void;
}

/** Default departure: the next whole hour, as a value <input type=datetime-local> accepts. */
function nextHourLocal(): string {
  const when = new Date();
  when.setMinutes(0, 0, 0);
  when.setHours(when.getHours() + 1);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}` +
         `T${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

/* Memoised. The forecast hour lives in App state, so without this every slider
   tick re-renders this whole subtree - measured at 14 fps while dragging, with
   the checkpoint table alone reconciling a few hundred elements per frame. Its
   props do not depend on the hour, so React.memo skips it entirely and the
   slider only re-renders what actually changes. */
export const TripForm = memo(function TripForm({ intervals, busy, onSubmit }: Props) {
  // Denver to Albuquerque: a real I-25 freight corridor, short enough to plan
  // quickly and varied enough that the three options are genuinely different.
  const [origin, setOrigin] = useState('Denver, CO');
  const [destination, setDestination] = useState('Albuquerque, NM');
  const [departure, setDeparture] = useState(nextHourLocal);
  const [loadLb, setLoadLb] = useState('45000');
  const [interval, setInterval] = useState(intervals[1] ?? 25);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    onSubmit({
      origin: origin.trim(),
      destination: destination.trim(),
      // datetime-local has no zone; the Date constructor reads it as local time,
      // and toISOString then sends an unambiguous instant to the backend.
      departure: new Date(departure).toISOString(),
      load_lb: Number(loadLb) || 0,
      interval_miles: interval,
    });
  };

  return (
    <form className="form" onSubmit={submit}>
      <label className="field">
        <span>Origin</span>
        <input
          value={origin}
          onChange={(e) => setOrigin(e.target.value)}
          placeholder="City, state"
          required
          autoComplete="off"
        />
      </label>

      <label className="field">
        <span>Destination</span>
        <input
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          placeholder="City, state"
          required
          autoComplete="off"
        />
      </label>

      <div className="row">
        <label className="field">
          <span>Departure</span>
          <input
            type="datetime-local"
            value={departure}
            onChange={(e) => setDeparture(e.target.value)}
            required
          />
        </label>

        <label className="field narrow">
          <span>Load (lb)</span>
          <input
            type="number"
            min={0}
            max={200000}
            step={500}
            value={loadLb}
            onChange={(e) => setLoadLb(e.target.value)}
            required
          />
        </label>
      </div>

      <fieldset className="field">
        <legend>Sample weather every</legend>
        <div className="segmented" role="radiogroup" aria-label="Sampling interval">
          {intervals.map((miles) => (
            <button
              key={miles}
              type="button"
              role="radio"
              aria-checked={interval === miles}
              className={interval === miles ? 'on' : ''}
              onClick={() => setInterval(miles)}
            >
              {miles} mi
            </button>
          ))}
        </div>
      </fieldset>

      <button className="primary" type="submit" disabled={busy}>
        {busy ? 'Checking the weather ahead…' : 'Plan trip'}
      </button>

      <p className="hint">
        Load weight changes the verdict: above 30,000 lb a 45–54 mph wind becomes
        No Travel rather than Severe.
      </p>
    </form>
  );
});
