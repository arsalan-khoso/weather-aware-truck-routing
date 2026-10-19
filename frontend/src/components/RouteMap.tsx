import { forwardRef, memo, useEffect, useImperativeHandle, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';

import type { Plan } from '../types';
import { ROUTE_COLORS, RISK_LEVEL_STOPS, formatClock } from '../risk';

/*
 * The map, and the reason the slider stays smooth.
 *
 * Three rules this component sticks to:
 *
 * 1. The map instance lives in a ref and is created exactly once. React never
 *    re-renders it. Every update below is an imperative call on that instance,
 *    so dragging the slider cannot trigger a React render of the map.
 *
 * 2. Moving the slider transfers nothing and recomputes nothing. Each corridor
 *    point already carries a weight for every hour as h0..h47, so changing the
 *    hour only changes which of those the map reads.
 *
 * 3. Nothing expensive is switched on: no terrain, no 3D extrusions, no raster
 *    layers, no fog. Three line layers, one circle layer and the heatmap.
 *
 * How the hour is changed, and why it looks odd
 * ---------------------------------------------
 * The obvious implementation is one heatmap layer whose `heatmap-weight` is
 * rewritten per hour with setPaintProperty. That was built first and measured on
 * the target machine - a two-core i7-6600U with integrated graphics - while
 * sweeping the slider:
 *
 *     map idle                                  60 fps
 *     panning the map, no app code involved     38 fps   <- hardware ceiling
 *     sweeping with setPaintProperty            27 fps
 *     sweeping by layer visibility              34 fps
 *
 * The 38 fps ceiling is the basemap redrawing at this canvas size; hiding the
 * heatmap, the routes and the checkpoints moved it by about two frames, so what
 * this app draws is close to free. The gap below the ceiling was the cost of
 * rewriting a data-driven paint property: mapbox re-evaluates the expression
 * across every feature and re-uploads the paint buffers each time.
 *
 * So one heatmap layer per hour is built up front, each with a fixed weight
 * expression, and changing the hour toggles visibility instead. The expressions
 * never change, so the buffers are built once and reused. It costs 48 layers and
 * about 80 ms at startup, and it recovers most of the distance to the ceiling.
 *
 * The last few frames came from taking React out of the drag path entirely.
 * Routing the hour through component state cost about 5 ms per change - a render,
 * a commit and an effect, for a value only the map consumes:
 *
 *     layer visibility driven through React state       28 fps
 *     the same call made imperatively                   34 fps
 *
 * So the slider drives `setHour` on this handle directly while the pointer is
 * down, and commits to React state once on release. State stays the source of
 * truth; it just is not in the sixty-times-a-second path.
 */

const STYLE = 'mapbox://styles/mapbox/light-v11';

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };

const SOURCE = { routes: 'routes', checkpoints: 'checkpoints', heat: 'corridor' } as const;
const LAYER = {
  casing: 'routes-casing',
  line: 'routes-line',
  checkpoints: 'checkpoint-dots',
} as const;

const HEAT_PREFIX = 'corridor-heat-';
const heatLayerId = (hour: number) => `${HEAT_PREFIX}${hour}`;

/* Shared by every hour layer. Only the weight differs between them. */
type HeatPaint = NonNullable<mapboxgl.HeatmapLayerSpecification['paint']>;

const HEAT_PAINT: Omit<HeatPaint, 'heatmap-weight'> = {
  'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 3, 0.8, 9, 1.4],
  // Corridor points sit about 25 miles apart, so the radius has to grow with
  // zoom for the band to read as continuous rather than as a row of dots.
  'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 3, 18, 6, 42, 9, 90],
  'heatmap-opacity': 0.75,
  'heatmap-color': [
    'interpolate', ['linear'], ['heatmap-density'],
    0.0, 'rgba(0,0,0,0)',
    0.15, 'rgba(22,163,74,0.35)',
    0.35, 'rgba(250,204,21,0.55)',
    0.55, 'rgba(251,146,60,0.70)',
    0.78, 'rgba(220,38,38,0.80)',
    1.0, 'rgba(109,40,217,0.90)',
  ],
};

interface Props {
  token: string;
  plan: Plan | null;
  selectedRoute: number;
  onSelectRoute: (index: number) => void;
  hour: number;
  showHeatmap: boolean;
}

/** Imperative surface, used for the one update that happens at pointer rate. */
export interface RouteMapHandle {
  /** Show this forecast hour now, bypassing React. */
  setHour: (hour: number) => void;
}

export const RouteMap = memo(forwardRef<RouteMapHandle, Props>(function RouteMap({
  token, plan, selectedRoute, onSelectRoute, hour, showHeatmap,
}: Props, handle) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<mapboxgl.Map | null>(null);
  const ready = useRef(false);
  const markers = useRef<mapboxgl.Marker[]>([]);
  const popup = useRef<mapboxgl.Popup | null>(null);
  const frame = useRef<number | null>(null);
  const builtHours = useRef(0);
  const shownHour = useRef(-1);
  const selectRef = useRef(onSelectRoute);
  selectRef.current = onSelectRoute;

  // --- create the map once -------------------------------------------------
  useEffect(() => {
    if (!container.current || map.current || !token) return;

    mapboxgl.accessToken = token;
    const instance = new mapboxgl.Map({
      container: container.current,
      style: STYLE,
      center: [-98.5, 39.8],
      zoom: 3.4,
      attributionControl: true,
      // Everything expensive is off. These are the defaults, but they are the
      // defaults that matter here, so they are stated rather than assumed.
      pitch: 0,
      bearing: 0,
      antialias: false,
    });
    map.current = instance;
    instance.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');
    instance.addControl(new mapboxgl.ScaleControl({ unit: 'imperial' }), 'bottom-left');

    instance.on('load', () => {
      instance.addSource(SOURCE.heat, { type: 'geojson', data: EMPTY });
      instance.addSource(SOURCE.routes, { type: 'geojson', data: EMPTY });
      instance.addSource(SOURCE.checkpoints, { type: 'geojson', data: EMPTY });

      instance.addLayer({
        id: LAYER.casing,
        type: 'line',
        source: SOURCE.routes,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#ffffff',
          'line-width': ['case', ['boolean', ['get', 'selected'], false], 9, 6],
          'line-opacity': 0.9,
        },
      });

      instance.addLayer({
        id: LAYER.line,
        type: 'line',
        source: SOURCE.routes,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['boolean', ['get', 'selected'], false], 5, 3],
          'line-opacity': ['case', ['boolean', ['get', 'selected'], false], 1, 0.55],
        },
      });

      instance.addLayer({
        id: LAYER.checkpoints,
        type: 'circle',
        source: SOURCE.checkpoints,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 3.5, 7, 6.5],
          'circle-color': ['interpolate', ['linear'], ['get', 'level'], ...RISK_LEVEL_STOPS],
          'circle-stroke-width': ['case', ['boolean', ['get', 'escalated'], false], 2, 1],
          'circle-stroke-color': ['case', ['boolean', ['get', 'escalated'], false], '#111827', '#ffffff'],
        },
      });

      instance.on('click', LAYER.line, (event) => {
        const index = event.features?.[0]?.properties?.index;
        if (typeof index === 'number') selectRef.current(index);
      });
      instance.on('click', LAYER.checkpoints, (event) => showCheckpoint(instance, event, popup));

      for (const layer of [LAYER.line, LAYER.checkpoints]) {
        instance.on('mouseenter', layer, () => { instance.getCanvas().style.cursor = 'pointer'; });
        instance.on('mouseleave', layer, () => { instance.getCanvas().style.cursor = ''; });
      }

      ready.current = true;
      instance.fire('app:ready');

      // Development only: a handle for profiling the map in isolation from
      // React. Stripped from the production bundle by the bundler.
      if (import.meta.env.DEV) {
        (window as unknown as { __truckMap?: mapboxgl.Map }).__truckMap = instance;
      }
    });

    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      popup.current?.remove();
      markers.current.forEach((m) => m.remove());
      markers.current = [];
      instance.remove();
      map.current = null;
      ready.current = false;
      builtHours.current = 0;
      shownHour.current = -1;
    };
  }, [token]);

  // --- push a new plan onto the map ---------------------------------------
  useEffect(() => {
    const instance = map.current;
    if (!instance || !plan) return;

    const apply = () => {
      (instance.getSource(SOURCE.routes) as mapboxgl.GeoJSONSource)?.setData(routeFeatures(plan, selectedRoute));
      (instance.getSource(SOURCE.checkpoints) as mapboxgl.GeoJSONSource)?.setData(checkpointFeatures(plan, selectedRoute));
      (instance.getSource(SOURCE.heat) as mapboxgl.GeoJSONSource)?.setData(corridorFeatures(plan));
      ensureHeatLayers(instance, plan.heatmap.hours, builtHours, shownHour);

      markers.current.forEach((m) => m.remove());
      markers.current = [plan.origin, plan.destination].map((place, i) =>
        new mapboxgl.Marker({ color: i === 0 ? '#0f172a' : '#be123c' })
          .setLngLat([place.lon, place.lat])
          .setPopup(new mapboxgl.Popup({ offset: 14 }).setText(place.name))
          .addTo(instance),
      );

      // Nothing is drawn over the map any more - the forecast slider and the
      // risk key live in the sidebar - so the route only needs an even margin.
      instance.fitBounds(planBounds(plan), { padding: 56, duration: 700 });
    };

    if (ready.current) apply();
    else instance.once('app:ready', apply);
  }, [plan]);  // selection is handled separately so selecting never reloads data

  // --- selection: restyle in place, never refetch ------------------------
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current || !plan) return;
    (instance.getSource(SOURCE.routes) as mapboxgl.GeoJSONSource)?.setData(routeFeatures(plan, selectedRoute));
    (instance.getSource(SOURCE.checkpoints) as mapboxgl.GeoJSONSource)?.setData(checkpointFeatures(plan, selectedRoute));
  }, [selectedRoute, plan]);

  // --- the slider: swap which hour layer is visible, once per frame -------
  //
  // `visible` is mirrored into a ref so the imperative path can read it without
  // the handle having to be rebuilt whenever the toggle changes.
  const heatOn = useRef(showHeatmap);
  heatOn.current = showHeatmap;

  useImperativeHandle(handle, () => ({
    setHour(next: number) {
      const instance = map.current;
      if (!instance || !ready.current || !builtHours.current) return;
      // Applied synchronously. Coalescing this into a requestAnimationFrame was
      // tried first and measured slower - mapbox already defers its own redraw
      // to the next frame, so the extra hop only added a frame of latency
      // without removing any work. Two visibility flags per event is cheap, and
      // repeated events inside one frame still produce a single repaint.
      showHour(instance, heatOn.current ? next : -1, builtHours, shownHour);
    },
  }), []);

  // Committed state still drives the map, for the first paint of a plan, for the
  // heatmap toggle, and for any change that does not come from dragging.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current || !builtHours.current) return;
    showHour(instance, showHeatmap ? hour : -1, builtHours, shownHour);
  }, [hour, showHeatmap, plan]);

  return <div ref={container} className="map" aria-label="Route map" />;
}));

/* ------------------------------------------------------------ heat layers */

/** One hidden heatmap layer per forecast hour, built once and then reused. */
function ensureHeatLayers(
  instance: mapboxgl.Map,
  hours: number,
  builtHours: React.MutableRefObject<number>,
  shownHour: React.MutableRefObject<number>,
) {
  if (builtHours.current === hours) return;

  for (let h = builtHours.current; h > hours; h -= 1) {
    if (instance.getLayer(heatLayerId(h - 1))) instance.removeLayer(heatLayerId(h - 1));
  }
  for (let h = 0; h < hours; h += 1) {
    if (instance.getLayer(heatLayerId(h))) continue;
    instance.addLayer({
      id: heatLayerId(h),
      type: 'heatmap',
      source: SOURCE.heat,
      layout: { visibility: 'none' },
      // The weight is fixed at creation; that is what keeps the paint buffers
      // from being rebuilt every time the hour changes.
      paint: { 'heatmap-weight': ['coalesce', ['get', `h${h}`], 0], ...HEAT_PAINT },
    } satisfies mapboxgl.HeatmapLayerSpecification, LAYER.casing);
  }
  builtHours.current = hours;
  shownHour.current = -1;
}

/** Show one hour's layer, or none when `hour` is negative. */
function showHour(
  instance: mapboxgl.Map,
  hour: number,
  builtHours: React.MutableRefObject<number>,
  shownHour: React.MutableRefObject<number>,
) {
  if (shownHour.current === hour) return;
  if (shownHour.current >= 0 && instance.getLayer(heatLayerId(shownHour.current))) {
    instance.setLayoutProperty(heatLayerId(shownHour.current), 'visibility', 'none');
  }
  if (hour >= 0 && hour < builtHours.current && instance.getLayer(heatLayerId(hour))) {
    instance.setLayoutProperty(heatLayerId(hour), 'visibility', 'visible');
  }
  shownHour.current = hour;
}

/* ----------------------------------------------------------------- features */

function routeFeatures(plan: Plan, selected: number): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: plan.routes.map((route) => ({
      type: 'Feature',
      id: route.index,
      properties: {
        index: route.index,
        color: ROUTE_COLORS[route.index % ROUTE_COLORS.length],
        selected: route.index === selected,
        recommended: route.index === plan.recommendation.recommended_index,
      },
      geometry: { type: 'LineString', coordinates: route.geometry },
    })),
  };
}

/** Only the selected route's checkpoints are drawn: three sets at once is noise. */
function checkpointFeatures(plan: Plan, selected: number): GeoJSON.FeatureCollection {
  const route = plan.routes.find((r) => r.index === selected) ?? plan.routes[0];
  if (!route) return EMPTY;
  return {
    type: 'FeatureCollection',
    features: route.checkpoints.map((point) => ({
      type: 'Feature',
      properties: {
        level: point.risk_level,
        risk: point.risk,
        miles: point.miles,
        eta: point.eta,
        reason: point.reason,
        escalated: point.load_escalated,
        wind: point.forecast.wind_mph,
        rain: point.forecast.rain_in_hr,
        snow: point.forecast.snow_in_hr,
      },
      geometry: { type: 'Point', coordinates: [point.lon, point.lat] },
    })),
  };
}

/** Corridor cells with every slider hour pre-attached as h0..h47. */
function corridorFeatures(plan: Plan): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: plan.heatmap.points.map((point) => {
      const properties: Record<string, number> = {};
      point.h.forEach((weight, index) => { properties[`h${index}`] = weight; });
      return {
        type: 'Feature',
        properties,
        geometry: { type: 'Point', coordinates: [point.lon, point.lat] },
      };
    }),
  };
}

function planBounds(plan: Plan): mapboxgl.LngLatBounds {
  const bounds = new mapboxgl.LngLatBounds();
  for (const route of plan.routes) for (const coord of route.geometry) bounds.extend(coord);
  bounds.extend([plan.origin.lon, plan.origin.lat]);
  bounds.extend([plan.destination.lon, plan.destination.lat]);
  return bounds;
}

function showCheckpoint(
  instance: mapboxgl.Map,
  event: mapboxgl.MapMouseEvent & { features?: mapboxgl.MapboxGeoJSONFeature[] },
  popup: React.MutableRefObject<mapboxgl.Popup | null>,
) {
  const properties = event.features?.[0]?.properties;
  if (!properties) return;

  const reading = (label: string, value: unknown, unit: string) =>
    value === null || value === undefined ? '' : `<div><span>${label}</span><b>${value} ${unit}</b></div>`;

  popup.current?.remove();
  popup.current = new mapboxgl.Popup({ offset: 12, maxWidth: '260px', closeButton: true })
    .setLngLat(event.lngLat)
    .setHTML(
      `<div class="cp-popup">
         <div class="cp-risk" data-risk="${properties.risk}">${properties.risk}</div>
         <div class="cp-head">Mile ${Math.round(properties.miles)} &middot; ETA ${formatClock(properties.eta)}</div>
         <div class="cp-reason">${properties.reason}</div>
         <div class="cp-grid">
           ${reading('Wind', properties.wind, 'mph')}
           ${reading('Rain', properties.rain, 'in/hr')}
           ${reading('Snow', properties.snow, 'in/hr')}
         </div>
         ${properties.escalated ? '<div class="cp-flag">Raised by load weight</div>' : ''}
       </div>`,
    )
    .addTo(instance);
}
