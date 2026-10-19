import { memo } from 'react';

import type { Plan, Route } from '../types';
import { RISK_COLORS, ROUTE_COLORS, formatClock, formatDuration, formatMiles } from '../risk';

/*
 * One card per route option: distance, drive time, arrival, and how its miles
 * break down by risk band. The recommended card carries the reason the ranking
 * chose it, so the recommendation is auditable rather than magic.
 *
 * Cards are listed in recommendation order, which is the order the ranking
 * produced, not the order the provider returned them in.
 */

const RISK_ORDER = ['Low', 'Moderate', 'High', 'Severe', 'No Travel'] as const;

interface Props {
  plan: Plan;
  selected: number;
  onSelect: (index: number) => void;
}

/* Memoised. The forecast hour lives in App state, so without this every slider
   tick re-renders this whole subtree - measured at 14 fps while dragging, with
   the checkpoint table alone reconciling a few hundred elements per frame. Its
   props do not depend on the hour, so React.memo skips it entirely and the
   slider only re-renders what actually changes. */
export const RouteSummary = memo(function RouteSummary({ plan, selected, onSelect }: Props) {
  const byIndex = new Map(plan.routes.map((route) => [route.index, route]));
  const ordered = plan.recommendation.order
    .map((index) => byIndex.get(index))
    .filter((route): route is Route => route !== undefined);

  return (
    <section className="routes">
      <h2>Route options</h2>
      {ordered.map((route) => (
        <RouteCard
          key={route.index}
          route={route}
          plan={plan}
          selected={route.index === selected}
          onSelect={onSelect}
        />
      ))}
    </section>
  );
});

function RouteCard({ route, plan, selected, onSelect }: {
  route: Route;
  plan: Plan;
  selected: boolean;
  onSelect: (index: number) => void;
}) {
  const recommended = route.index === plan.recommendation.recommended_index;
  const color = ROUTE_COLORS[route.index % ROUTE_COLORS.length];
  const { summary } = route;

  return (
    <article
      className={`card${selected ? ' selected' : ''}${recommended ? ' recommended' : ''}`}
      onClick={() => onSelect(route.index)}
      aria-current={selected}
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(route.index); } }}
    >
      <header>
        <span className="swatch" style={{ background: color }} aria-hidden />
        <h3>Route {route.index + 1}</h3>
        {recommended && <span className="badge rec">Recommended</span>}
        {!summary.viable && <span className="badge stop">Do not drive</span>}
      </header>

      <dl className="stats">
        <div><dt>Distance</dt><dd>{formatMiles(route.distance_miles)}</dd></div>
        <div><dt>Drive time</dt><dd>{formatDuration(route.duration_s)}</dd></div>
        <div><dt>Arrives</dt><dd>{formatClock(route.arrival)}</dd></div>
        <div><dt>Worst</dt><dd style={{ color: RISK_COLORS[summary.worst] }}>{summary.worst}</dd></div>
      </dl>

      <MileBar summary={summary} total={route.distance_miles} />

      <ul className="risk-legend">
        {RISK_ORDER.filter((band) => (summary.miles_by_risk[band] ?? 0) > 0).map((band) => (
          <li key={band}>
            <i style={{ background: RISK_COLORS[band] }} aria-hidden />
            {band} <b>{Math.round(summary.miles_by_risk[band] ?? 0)} mi</b>
          </li>
        ))}
      </ul>

      {recommended && (
        <p className="reason">
          <strong>Why:</strong> {plan.recommendation.reason}
        </p>
      )}
    </article>
  );
}

/** Stacked bar of the route's miles by risk band, in severity order. */
function MileBar({ summary, total }: { summary: Route['summary']; total: number }) {
  const miles = RISK_ORDER.map((band) => ({ band, miles: summary.miles_by_risk[band] ?? 0 }))
    .filter((part) => part.miles > 0);
  const sum = miles.reduce((acc, part) => acc + part.miles, 0) || total || 1;

  return (
    <div
      className="milebar"
      role="img"
      aria-label={miles.map((p) => `${Math.round(p.miles)} miles ${p.band}`).join(', ')}
    >
      {miles.map((part) => (
        <span
          key={part.band}
          style={{ width: `${(part.miles / sum) * 100}%`, background: RISK_COLORS[part.band] }}
          title={`${Math.round(part.miles)} mi ${part.band}`}
        />
      ))}
    </div>
  );
}
