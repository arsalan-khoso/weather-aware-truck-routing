/** Shapes returned by the Django API. Mirrors `backend/routing/planner.py`. */

export const RISK_LEVELS = ['Low', 'Moderate', 'High', 'Severe', 'No Travel'] as const;
export type RiskLabel = (typeof RISK_LEVELS)[number];

export interface Place {
  name: string;
  lon: number;
  lat: number;
}

export interface Forecast {
  wind_mph: number | null;
  rain_in_hr: number | null;
  snow_in_hr: number | null;
}

export interface Checkpoint {
  index: number;
  lon: number;
  lat: number;
  miles: number;
  /** ISO timestamp: when the truck reaches this point. */
  eta: string;
  /** Miles of road this checkpoint stands for, used for severe-mile totals. */
  segment_miles: number;
  risk: RiskLabel;
  risk_level: number;
  /** Which of wind / rain / snow / load set the risk. */
  driver: string;
  reason: string;
  load_escalated: boolean;
  bands: Record<string, RiskLabel>;
  forecast: Forecast;
  has_forecast: boolean;
}

export interface RouteSummary {
  worst: RiskLabel;
  viable: boolean;
  avg_risk: number;
  mean_risk: number;
  no_travel_miles: number;
  severe_miles: number;
  high_miles: number;
  miles_by_risk: Partial<Record<RiskLabel, number>>;
  checkpoint_count: number;
}

export interface Route {
  index: number;
  /** How the option was obtained: provider alternative, via-offset, or no-motorway. */
  source: string;
  via: [number, number] | null;
  /** Simplified for drawing; all distances and times came from the full geometry. */
  geometry: [number, number][];
  distance_miles: number;
  duration_s: number;
  arrival: string;
  summary: RouteSummary;
  checkpoints: Checkpoint[];
}

export interface Recommendation {
  recommended_index: number | null;
  order: number[];
  decided_by: string | null;
  reason: string;
  viable?: boolean;
}

/** One corridor cell, carrying a 0..1 risk weight for every slider hour. */
export interface HeatmapPoint {
  lon: number;
  lat: number;
  h: number[];
}

export interface Heatmap {
  hours: number;
  start: string;
  /** ISO timestamp per slider hour, for the label. */
  times: string[];
  points: HeatmapPoint[];
}

export interface Plan {
  origin: Place;
  destination: Place;
  departure: string;
  load_lb: number;
  interval_miles: number;
  routes: Route[];
  recommendation: Recommendation;
  heatmap: Heatmap;
  warnings: string[];
}

export interface AppConfig {
  mapbox_token: string;
  intervals_miles: number[];
  slider_hours: number;
}

export interface PlanRequest {
  origin: string;
  destination: string;
  /** ISO 8601, with offset. */
  departure: string;
  load_lb: number;
  interval_miles: number;
}
