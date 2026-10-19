import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, getConfig, planTrip } from './api/client';
import { CheckpointList } from './components/CheckpointList';
import { HeatmapSlider } from './components/HeatmapSlider';
import { RouteMap } from './components/RouteMap';
import type { RouteMapHandle } from './components/RouteMap';
import { RouteSummary } from './components/RouteSummary';
import { TripForm } from './components/TripForm';
import { useFrameRate } from './hooks/useFrameRate';
import { RISK_COLORS } from './risk';
import type { AppConfig, Plan, PlanRequest, RiskLabel } from './types';

/*
 * Page shell and the only place application state lives.
 *
 * The map is deliberately given primitives - a plan, a selected index, an hour -
 * rather than callbacks that change on every render, so moving the slider cannot
 * cause it to tear down or rebuild anything.
 */

const RISK_BANDS: RiskLabel[] = ['Low', 'Moderate', 'High', 'Severe', 'No Travel'];
const DEFAULT_INTERVALS = [10, 25, 50];

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [hour, setHour] = useState(0);
  const [showHeatmap, setShowHeatmap] = useState(true);

  const inFlight = useRef<AbortController | null>(null);
  const mapHandle = useRef<RouteMapHandle>(null);
  const showFps = new URLSearchParams(window.location.search).has('fps');
  const { fps, low } = useFrameRate(showFps);

  // Memoised so the fallback is not a fresh array on every render. Without this
  // the prop changes identity each time the hour does, and TripForm's React.memo
  // never gets a chance to skip - which is the whole point of memoising it.
  const intervals = useMemo(
    () => config?.intervals_miles ?? DEFAULT_INTERVALS,
    [config],
  );

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch((cause: unknown) => {
        setError(cause instanceof ApiError ? cause.message : 'Could not load configuration.');
      });
  }, []);

  const submit = useCallback((request: PlanRequest) => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;

    setBusy(true);
    setError(null);

    planTrip(request, controller.signal)
      .then((result) => {
        setPlan(result);
        setSelected(result.recommendation.recommended_index ?? 0);
        setHour(0);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof ApiError ? cause.message : 'Could not plan this trip.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
  }, []);

  // While the slider is being dragged the hour goes straight to the map; state
  // catches up on release. Both callbacks are stable, so the memoised slider
  // never re-renders because of them.
  const liveHour = useCallback((next: number) => mapHandle.current?.setHour(next), []);
  const route = plan?.routes.find((r) => r.index === selected) ?? plan?.routes[0];

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <h1>Weather-Aware Truck Routing</h1>
          <p>Three routes, scored on the weather each one will actually meet.</p>
        </header>

        <TripForm intervals={intervals} busy={busy} onSubmit={submit} />

        {error && <div className="alert" role="alert">{error}</div>}

        {plan?.warnings.map((warning) => (
          <div className="alert soft" key={warning} role="status">{warning}</div>
        ))}

        {plan && <RouteSummary plan={plan} selected={selected} onSelect={setSelected} />}
        {plan && route && (
          <CheckpointList route={route} intervalMiles={plan.interval_miles} />
        )}

        <footer className="credits">
          Routing and geocoding by Mapbox &middot; forecasts by Open-Meteo
        </footer>
      </aside>

      <main className="stage">
        {config ? (
          <RouteMap
            ref={mapHandle}
            token={config.mapbox_token}
            plan={plan}
            selectedRoute={selected}
            onSelectRoute={setSelected}
            hour={hour}
            showHeatmap={showHeatmap}
          />
        ) : (
          <div className="stage-empty">{error ?? 'Loading map…'}</div>
        )}

        {busy && <div className="stage-busy" role="status">Sampling the forecast along each route…</div>}

        <div className="overlay">
          <Legend />
          {plan && (
            <HeatmapSlider
              heatmap={plan.heatmap}
              hour={hour}
              onLive={liveHour}
              onCommit={setHour}
              visible={showHeatmap}
              onVisible={setShowHeatmap}
            />
          )}
        </div>

        {showFps && (
          <div className="fps" role="status">
            {fps} fps <span className="muted">(low {Number.isFinite(low) ? low : '–'})</span>
          </div>
        )}
      </main>
    </div>
  );
}

const Legend = memo(function Legend() {
  return (
    <div className="legend">
      <span className="legend-title">Checkpoint risk</span>
      <ul>
        {RISK_BANDS.map((band) => (
          <li key={band}>
            <i style={{ background: RISK_COLORS[band] }} aria-hidden />
            {band}
          </li>
        ))}
      </ul>
    </div>
  );
});
