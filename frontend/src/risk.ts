import type { RiskLabel } from './types';

/* Risk palette, shared by the map, the summaries and the legend so one risk
   level never shows two different colours. Green through red is the convention
   drivers already read; No Travel breaks out of the ramp into purple because it
   is not "worse weather", it is a different instruction. */
export const RISK_COLORS: Record<RiskLabel, string> = {
  Low: '#16a34a',
  Moderate: '#facc15',
  High: '#fb923c',
  Severe: '#dc2626',
  'No Travel': '#6d28d9',
};

export const RISK_TEXT_ON: Record<RiskLabel, string> = {
  Low: '#ffffff',
  Moderate: '#1c1917',
  High: '#1c1917',
  Severe: '#ffffff',
  'No Travel': '#ffffff',
};

/** Mapbox expression stops for colouring a feature by its numeric risk level. */
export const RISK_LEVEL_STOPS = [
  0, RISK_COLORS.Low,
  1, RISK_COLORS.Moderate,
  2, RISK_COLORS.High,
  3, RISK_COLORS.Severe,
  4, RISK_COLORS['No Travel'],
] as const;

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds / 60);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return hours ? `${hours}h ${String(minutes).padStart(2, '0')}m` : `${minutes}m`;
}

export function formatMiles(miles: number): string {
  return `${Math.round(miles).toLocaleString('en-US')} mi`;
}

export function formatClock(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function formatHourLabel(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    weekday: 'short',
    hour: 'numeric',
  });
}

/* Route identity colours. Deliberately cool and neutral so they can never be
   mistaken for the warm risk ramp above: a blue line means "route 1", it does
   not mean "low risk". The sidebar cards use the same colours as the map lines. */
export const ROUTE_COLORS = ['#2563eb', '#0891b2', '#475569'] as const;
