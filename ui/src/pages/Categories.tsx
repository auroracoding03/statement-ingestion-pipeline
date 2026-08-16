import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PeriodPicker, type PeriodValue } from "../components/PeriodPicker";
import { Empty, ErrorNote, Loading, Metric, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { compactMoney, money } from "../lib/format";
import { monthsInRange, resolveClientPeriod } from "../lib/period";
import { hashHref } from "../lib/router";
import { sortedLabels } from "../lib/sort";
import type { TagSpendBreakdown, TagSpendItem } from "../lib/types";
import { useAsync } from "../lib/useAsync";

type SpendView = "category" | "trips";

function norm(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

function bucketTotal(breakdown: TagSpendBreakdown[], match: (row: TagSpendBreakdown) => boolean) {
  return breakdown.filter(match).reduce(
    (acc, row) => ({ total: acc.total + row.total, txn_count: acc.txn_count + row.txn_count }),
    { total: 0, txn_count: 0 },
  );
}

function tripBuckets(breakdown: TagSpendBreakdown[]) {
  return {
    lodging: bucketTotal(breakdown, (row) => norm(row.category) === "travel" && norm(row.subcategory) === "lodging"),
    transport: bucketTotal(
      breakdown,
      (row) =>
        (norm(row.category) === "travel" && norm(row.subcategory) === "transit") ||
        norm(row.category) === "transport",
    ),
    food: bucketTotal(breakdown, (row) => norm(row.category) === "food"),
    activities: bucketTotal(
      breakdown,
      (row) => norm(row.category) === "travel" && norm(row.subcategory) === "activity",
    ),
  };
}

export function Categories() {
  const [view, setView] = useState<SpendView>("category");
  const { data, loading, error } = useAsync(() => api.categoriesMonthly(), []);
  const tripsState = useAsync(() => api.tagSpend({ kind: "trip" }), []);
  const rows = data ?? [];
  const [selected, setSelected] = useState<string>("");
  const [selectedTrip, setSelectedTrip] = useState<string>("");
  const [period, setPeriod] = useState<PeriodValue>({ preset: "t12m", month: "", since: "", until: "" });

  const months = useMemo(
    () => [...new Set(rows.map((row) => row.month))].sort(),
    [rows],
  );
  const resolved = resolveClientPeriod({ ...period, months });
  const inRange = useMemo(
    () => new Set(monthsInRange(months, resolved.since, resolved.until)),
    [months, resolved.since, resolved.until],
  );
  const scoped = useMemo(
    () => rows.filter((row) => inRange.has(row.month)),
    [rows, inRange],
  );

  const categories = useMemo(
    () => sortedLabels(new Set(scoped.map((row) => row.category))),
    [scoped],
  );

  const totals = useMemo(() => {
    const map = new Map<string, { total: number; txn_count: number }>();
    for (const row of scoped) {
      const prev = map.get(row.category) ?? { total: 0, txn_count: 0 };
      map.set(row.category, {
        total: prev.total + row.total,
        txn_count: prev.txn_count + row.txn_count,
      });
    }
    return [...map.entries()].sort((a, b) => b[1].total - a[1].total);
  }, [scoped]);

  const trend = useMemo(() => {
    const active = selected || totals[0]?.[0];
    if (!active) return { active: "", data: [] };
    const byMonth = new Map<string, number>();
    for (const row of scoped.filter((item) => item.category === active)) {
      byMonth.set(row.month, (byMonth.get(row.month) ?? 0) + row.total);
    }
    return {
      active,
      data: [...byMonth.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([month, total]) => ({ month, total })),
    };
  }, [scoped, selected, totals]);

  const trips = tripsState.data?.items ?? [];
  const tripTotal = trips.reduce((sum, item) => sum + item.total, 0);
  const activeTrip =
    trips.find((item) => item.id === selectedTrip) ?? trips[0] ?? null;

  return (
    <>
      <PageHeader title="Spend" lede="Category totals and trip cost from the live ledger." />

      <div className="toolbar">
        <button
          type="button"
          className={`btn${view === "category" ? " selected" : " subtle"}`}
          onClick={() => setView("category")}
        >
          Category
        </button>
        <button
          type="button"
          className={`btn${view === "trips" ? " selected" : " subtle"}`}
          onClick={() => setView("trips")}
        >
          Trips
        </button>
      </div>

      {view === "category" && (
        <CategorySpend
          error={error}
          loading={loading}
          rowsLength={rows.length}
          months={months}
          period={period}
          onPeriodChange={setPeriod}
          trend={trend}
          categories={categories}
          onSelect={setSelected}
          totals={totals}
          resolved={resolved}
        />
      )}

      {view === "trips" && (
        <TripSpend
          error={tripsState.error}
          loading={tripsState.loading}
          trips={trips}
          tripTotal={tripTotal}
          activeTrip={activeTrip}
          onSelect={setSelectedTrip}
        />
      )}
    </>
  );
}

function CategorySpend({
  error,
  loading,
  rowsLength,
  months,
  period,
  onPeriodChange,
  trend,
  categories,
  onSelect,
  totals,
  resolved,
}: {
  error: string | null;
  loading: boolean;
  rowsLength: number;
  months: string[];
  period: PeriodValue;
  onPeriodChange: (value: PeriodValue) => void;
  trend: { active: string; data: { month: string; total: number }[] };
  categories: string[];
  onSelect: (category: string) => void;
  totals: [string, { total: number; txn_count: number }][];
  resolved: { since: string; until: string };
}) {
  return (
    <>
      {error && <ErrorNote error={error} />}
      {loading && <Loading what="categories" />}
      {!loading && rowsLength === 0 && <Empty>No categorized spend yet.</Empty>}

      {months.length > 0 && (
        <div className="toolbar overview-controls">
          <PeriodPicker months={months} value={period} onChange={onPeriodChange} />
        </div>
      )}

      {trend.data.length > 0 && (
        <>
          <div className="toolbar">
            <label className="muted" htmlFor="cat-select">
              Trend for
            </label>
            <select
              id="cat-select"
              value={trend.active}
              onChange={(event) => onSelect(event.target.value)}
            >
              {categories.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </div>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={trend.data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e7dfd2" vertical={false} />
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} tickFormatter={(value) => compactMoney(Number(value))} />
                <Tooltip
                  formatter={(value) => money(Number(value))}
                  contentStyle={{ fontSize: 13, borderRadius: 4 }}
                />
                <Line
                  type="monotone"
                  dataKey="total"
                  stroke="#0f5c4c"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}

      {totals.length > 0 && (
        <>
          <h2>Category totals</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Category</th>
                  <th className="num">Total</th>
                  <th className="num">Transactions</th>
                  <th className="num">Average</th>
                </tr>
              </thead>
              <tbody>
                {totals.map(([category, agg]) => (
                  <tr
                    key={category}
                    className="clickable-row"
                    onClick={() => {
                      window.location.hash = hashHref("/transactions", {
                        category,
                        since: resolved.since,
                        until: resolved.until,
                      });
                    }}
                  >
                    <td>{category}</td>
                    <td className="num">{money(agg.total)}</td>
                    <td className="num">{agg.txn_count}</td>
                    <td className="num">{money(agg.total / agg.txn_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function TripSpend({
  error,
  loading,
  trips,
  tripTotal,
  activeTrip,
  onSelect,
}: {
  error: string | null;
  loading: boolean;
  trips: TagSpendItem[];
  tripTotal: number;
  activeTrip: TagSpendItem | null;
  onSelect: (id: string) => void;
}) {
  const buckets = activeTrip ? tripBuckets(activeTrip.breakdown) : null;
  const tiles = buckets
    ? [
        { key: "lodging", label: "Lodging", ...buckets.lodging },
        { key: "transport", label: "Transport", ...buckets.transport },
        { key: "food", label: "Food", ...buckets.food },
        { key: "activities", label: "Activities", ...buckets.activities },
      ].filter((tile) => tile.txn_count > 0 || tile.total !== 0)
    : [];

  return (
    <>
      {error && <ErrorNote error={error} />}
      {loading && <Loading what="trips" />}
      {!loading && trips.length === 0 && <Empty>No trip tags yet. Add a trip tag, then apply it to charges.</Empty>}

      {trips.length > 0 && (
        <>
          <div className="metrics">
            <Metric label="Total trip cost" value={money(tripTotal)} />
          </div>

          <h2>Trips</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Trip</th>
                  <th className="num">Total</th>
                  <th className="num">Charges</th>
                </tr>
              </thead>
              <tbody>
                {trips.map((trip) => (
                  <tr
                    key={trip.id}
                    className={`clickable-row${activeTrip?.id === trip.id ? " selected-row" : ""}`}
                    onClick={() => onSelect(trip.id)}
                  >
                    <td>{trip.label}</td>
                    <td className="num">{money(trip.total)}</td>
                    <td className="num">{trip.txn_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {activeTrip && (
        <>
          <div className="toolbar">
            <h2>{activeTrip.label}</h2>
            <a className="btn subtle" href={hashHref("/transactions", { tag: activeTrip.id })}>
              View transactions
            </a>
          </div>
          <div className="metrics">
            <Metric label="Total trip cost" value={money(activeTrip.total)} />
            <Metric label="Charges" value={activeTrip.txn_count} />
            {tiles.map((tile) => (
              <Metric key={tile.key} label={tile.label} value={money(tile.total)} />
            ))}
          </div>

          {activeTrip.breakdown.length > 0 && (
            <>
              <h2>Breakdown</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Category</th>
                      <th>Subcategory</th>
                      <th className="num">Total</th>
                      <th className="num">Transactions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeTrip.breakdown.map((row) => (
                      <tr
                        key={`${row.category}::${row.subcategory}`}
                        className="clickable-row"
                        onClick={() => {
                          window.location.hash = hashHref("/transactions", {
                            tag: activeTrip.id,
                            category: row.category === "Uncategorized" ? "" : row.category,
                            subcategory: row.subcategory,
                          });
                        }}
                      >
                        <td>{row.category}</td>
                        <td>{row.subcategory || "—"}</td>
                        <td className="num">{money(row.total)}</td>
                        <td className="num">{row.txn_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </>
  );
}
