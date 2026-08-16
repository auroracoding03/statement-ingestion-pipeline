import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PeriodPicker, type PeriodValue } from "../components/PeriodPicker";
import { Empty, ErrorNote, Loading, Metric, PageHeader, StatusPill } from "../components/ui";
import { api, canWrite } from "../lib/dataSource";
import { compactMoney, money, shortDate } from "../lib/format";
import { enumerateMonths, resolveClientPeriod } from "../lib/period";
import { hashHref } from "../lib/router";
import { sortedLabels } from "../lib/sort";
import type { CategoryMonthly, OverviewMonth } from "../lib/types";
import { useAsync } from "../lib/useAsync";

const COLORS = [
  "#0f5c4c",
  "#8a4b12",
  "#3d5a80",
  "#8b1e1e",
  "#5b7553",
  "#9c6644",
  "#4a4e69",
  "#2a9d8f",
  "#6d597a",
  "#b5651d",
];

export function Overview() {
  const status = useAsync(() => api.status(), []);
  const [period, setPeriod] = useState<PeriodValue>({ preset: "month", month: "", since: "", until: "" });
  const [cardholder, setCardholder] = useState("");
  const ready = period.preset !== "custom" || Boolean(period.since && period.until);
  const summary = useAsync(
    () =>
      ready
        ? api.overviewMonth({
            preset: period.preset,
            month: period.month || undefined,
            since: period.preset === "custom" ? period.since : undefined,
            until: period.preset === "custom" ? period.until : undefined,
            cardholder: cardholder || undefined,
          })
        : Promise.resolve(null),
    [period.preset, period.month, period.since, period.until, cardholder, ready],
  );

  const data = summary.data;
  const months = data?.months ?? [];
  const holders = status.data?.cardholders ?? [];
  const range = { since: data?.since ?? "", until: data?.until ?? "" };
  const chartRows = useMemo(
    () => (data?.categories ?? []).filter((row) => row.total > 0),
    [data],
  );
  const movers = useMemo(() => {
    return [...(data?.categories ?? [])]
      .filter((row) => row.delta != null && row.delta !== 0)
      .sort((a, b) => Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0))
      .slice(0, 6);
  }, [data]);

  function txnHref(extra: Record<string, string | number | boolean | undefined> = {}) {
    return hashHref("/transactions", { since: range.since, until: range.until, ...extra });
  }

  return (
    <>
      <PageHeader
        title="Monthly finance overview"
        lede="Pick a window for household surplus: income minus card spend and bank bills. Transfers between your own accounts are ignored."
      />

      {status.error && <ErrorNote error={status.error} />}
      {status.loading && <Loading what="status" />}

      {status.data && (
        <div className="metrics">
          <Metric label="Transactions" value={status.data.counts.total ?? 0} />
          <Metric label="Canonical merchants" value={status.data.canonical_merchants} />
          <Metric label="Unknown merchants" value={status.data.unknown_merchants} />
          <Metric
            label="Needs review"
            value={
              canWrite && status.data.review_pending > 0 ? (
                <a href={hashHref("/review")}>{status.data.review_pending}</a>
              ) : (
                status.data.review_pending
              )
            }
          />
          {canWrite && (
            <Metric
              label="Local AI"
              value={status.data.ollama_available ? "online" : "offline"}
            />
          )}
        </div>
      )}

      <div className="toolbar overview-controls">
        <PeriodPicker months={months} value={period} onChange={setPeriod} />
        {canWrite && holders.length > 0 && (
          <label>
            Spend by
            <select value={cardholder} onChange={(event) => setCardholder(event.target.value)}>
              <option value="">All</option>
              {holders.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {summary.error && <ErrorNote error={summary.error} />}
      {summary.loading && <Loading what="period summary" />}
      {!ready && <p className="muted">Choose a start and end date.</p>}

      {data && !summary.loading && (
        <>
          <div className="metrics">
            <Metric label={data.label ? `Spend · ${data.label}` : "Spend"} value={money(data.spend_total)} />
            <Metric label={deltaCaption(data)} value={deltaLabel(data)} />
            <Metric label="Income" value={money(data.income_total ?? 0)} />
            <Metric label="Surplus" value={signedMoney(data.surplus ?? 0)} />
            <Metric label="Returns" value={money(data.returns_total)} />
            <Metric label="Monthly payments" value={money(data.payments_total)} />
            <Metric label="Charges" value={data.charge_count} />
            <Metric
              label="Uncategorized"
              value={
                <a href={txnHref({ unclassified: "1" })}>
                  {`${money(data.uncategorized_total)} · ${data.uncategorized_count}`}
                </a>
              }
            />
            <Metric
              label="Needs review"
              value={
                canWrite && data.review_count > 0 ? (
                  <a href={hashHref("/review")}>{data.review_count}</a>
                ) : (
                  data.review_count
                )
              }
            />
          </div>

          {data.holders.length > 0 && (
            <div className="holder-split">
              {data.holders.map((row) => (
                <span key={row.name}>
                  <strong>{row.name}</strong> {money(row.total)}
                </span>
              ))}
            </div>
          )}

          <div className="overview-charts">
            <section>
              <h2>Spend by category</h2>
              <p className="chart-caption">This window’s household expenses (cards plus bank bills; transfers ignored). Click a bar to open those transactions.</p>
              {chartRows.length > 0 ? (
                <div className="chart-wrap">
                  <ResponsiveContainer width="100%" height={Math.max(220, chartRows.length * 28)}>
                    <BarChart data={chartRows} layout="vertical" margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7dfd2" horizontal={false} />
                      <XAxis type="number" tick={{ fontSize: 12 }} tickFormatter={(value) => compactMoney(Number(value))} />
                      <YAxis type="category" dataKey="category" width={120} tick={{ fontSize: 12 }} />
                      <Tooltip
                        formatter={(value) => [money(Number(value)), "Spend"]}
                        contentStyle={{ fontSize: 13, borderRadius: 4 }}
                      />
                      <Bar
                        dataKey="total"
                        fill={COLORS[0]}
                        radius={[0, 3, 3, 0]}
                        cursor="pointer"
                        onClick={(row) => {
                          const payload = row as { category?: string; payload?: { category?: string } };
                          const category = payload.category ?? payload.payload?.category;
                          if (category) window.location.hash = txnHref({ category });
                        }}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p className="muted">No categorized spend for this window{cardholder ? ` for ${cardholder}` : ""}.</p>
              )}
            </section>

            {movers.length > 0 && (
              <section>
                <h2>What changed</h2>
                <p className="chart-caption">
                  {deltaCaption(data)}. Red is higher spend, pine is lower. Click a bar to drill in.
                </p>
                <div className="chart-wrap">
                  <ResponsiveContainer width="100%" height={Math.max(200, movers.length * 36)}>
                    <BarChart data={movers} layout="vertical" margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e7dfd2" horizontal={false} />
                      <XAxis type="number" tick={{ fontSize: 12 }} tickFormatter={(value) => compactMoney(Number(value))} />
                      <YAxis type="category" dataKey="category" width={120} tick={{ fontSize: 12 }} />
                      <Tooltip
                        formatter={(value, _name, item) => {
                          const row = item?.payload as { total?: number; delta?: number } | undefined;
                          return [
                            `${signedMoney(Number(value))} · this window ${money(row?.total)}`,
                            "Change",
                          ];
                        }}
                        contentStyle={{ fontSize: 13, borderRadius: 4 }}
                      />
                      <Bar
                        dataKey="delta"
                        radius={[0, 3, 3, 0]}
                        cursor="pointer"
                        onClick={(row) => {
                          const payload = row as { category?: string; payload?: { category?: string } };
                          const category = payload.category ?? payload.payload?.category;
                          if (category) window.location.hash = txnHref({ category });
                        }}
                      >
                        {movers.map((row) => (
                          <Cell
                            key={row.category}
                            fill={(row.delta ?? 0) > 0 ? "#8b1e1e" : "#0f5c4c"}
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>
            )}
          </div>

          <div className="overview-lists">
            {data.large_charges.length > 0 && (
              <section>
                <h2>Large charges</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Merchant</th>
                        <th className="num">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.large_charges.map((row) => (
                        <tr
                          key={`${row.posted_date}-${row.merchant}-${row.amount}`}
                          className="clickable-row"
                          onClick={() => {
                            window.location.hash = txnHref({ q: row.merchant });
                          }}
                        >
                          <td>{shortDate(row.posted_date)}</td>
                          <td>
                            {row.merchant}
                            <div className="muted">
                              {[row.category, row.cardholder].filter(Boolean).join(" · ")}
                            </div>
                          </td>
                          <td className="num">{money(row.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {data.tagged.length > 0 && (
              <section>
                <h2>Trips & occasions</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Tag</th>
                        <th className="num">Spend</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.tagged.map((row) => (
                        <tr
                          key={row.id}
                          className="clickable-row"
                          onClick={() => {
                            window.location.hash = txnHref({ tag: row.id });
                          }}
                        >
                          <td>
                            {row.label}
                            <div className="muted">{row.kind}</div>
                          </td>
                          <td className="num">{money(row.total)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {data.bills.length > 0 && (
              <section>
                <h2>Bills this month</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Bill</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.bills.map((row) => (
                        <tr key={row.bill}>
                          <td>{row.bill}</td>
                          <td>
                            <StatusPill status={row.status} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </div>

          {(data.budget_rows ?? []).length > 0 && (
            <section className="overview-budget">
              <h2>Budget vs actual</h2>
              <p className="chart-caption">
                Envelopes marked Show on Overview. Budgeted and actual spend are in black; overspend is red,
                underspend is pine. Click a row to open those transactions.
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Category</th>
                      <th className="num">Budgeted</th>
                      <th className="num">Actual</th>
                      <th className="num">Variance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.budget_rows ?? []).map((row) => (
                      <tr
                        key={`${row.category}-${row.subcategory ?? ""}`}
                        className={`clickable-row${row.subcategory ? " budget-sub" : ""}`}
                        onClick={() => {
                          window.location.hash = txnHref({
                            category: row.category,
                            subcategory: row.subcategory ?? undefined,
                          });
                        }}
                      >
                        <td>{row.subcategory ? row.label : row.category}</td>
                        <td className="num">{money(row.budget)}</td>
                        <td className="num">{money(row.actual)}</td>
                        <td className={`num ${varianceClass(row.variance)}`}>{signedMoney(row.variance)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          <CategorySpendTrend
            cardholder={cardholder}
            defaultCategory={
              (data.budget_rows ?? []).find((row) => !row.subcategory)?.category
              ?? (data.budget_rows ?? [])[0]?.category
              ?? ""
            }
          />
        </>
      )}
    </>
  );
}

function topCategoryBySpend(rows: CategoryMonthly[]): string {
  const totals = new Map<string, number>();
  for (const row of rows) {
    totals.set(row.category, (totals.get(row.category) ?? 0) + row.total);
  }
  let best = "";
  let bestTotal = -Infinity;
  for (const [category, total] of totals) {
    if (total > bestTotal) {
      best = category;
      bestTotal = total;
    }
  }
  return best;
}

function CategorySpendTrend({
  cardholder,
  defaultCategory,
}: {
  cardholder: string;
  defaultCategory: string;
}) {
  const monthly = useAsync(
    () => api.categoriesMonthly({ cardholder: cardholder || undefined }),
    [cardholder],
  );
  const rules = useAsync(
    () =>
      canWrite
        ? api.rules()
        : Promise.resolve({ categories: [] as string[], subcategories: {} as Record<string, string[]>, rules: [] }),
    [],
  );
  const [period, setPeriod] = useState<PeriodValue>({ preset: "t12m", month: "", since: "", until: "" });
  const [category, setCategory] = useState("");
  const [subcategory, setSubcategory] = useState("");

  const rows = monthly.data ?? [];
  const months = useMemo(() => [...new Set(rows.map((row) => row.month))].sort(), [rows]);
  const ready = period.preset !== "custom" || Boolean(period.since && period.until);
  const resolved = resolveClientPeriod({ ...period, months });
  const timeline = useMemo(
    () => (ready ? enumerateMonths(resolved.since, resolved.until) : []),
    [ready, resolved.since, resolved.until],
  );

  const categories = useMemo(() => {
    const labels = new Set(rows.map((row) => row.category));
    if (defaultCategory) labels.add(defaultCategory);
    return sortedLabels(labels);
  }, [rows, defaultCategory]);

  const activeCategory = category || defaultCategory || topCategoryBySpend(rows);

  const subcategoryOptions = useMemo(() => {
    if (!activeCategory) return [];
    const fromData = rows
      .filter((row) => row.category === activeCategory)
      .map((row) => (row.subcategory ?? "").trim())
      .filter(Boolean);
    const fromRules = rules.data?.subcategories?.[activeCategory] ?? [];
    return sortedLabels(new Set([...fromData, ...fromRules]));
  }, [rows, activeCategory, rules.data]);

  const series = useMemo(() => {
    if (!activeCategory || timeline.length === 0) return [];
    const byMonth = new Map<string, number>();
    for (const month of timeline) byMonth.set(month, 0);
    for (const row of rows) {
      if (row.category !== activeCategory) continue;
      if (subcategory && (row.subcategory ?? "") !== subcategory) continue;
      if (!byMonth.has(row.month)) continue;
      byMonth.set(row.month, (byMonth.get(row.month) ?? 0) + row.total);
    }
    return timeline.map((month) => ({ month, total: byMonth.get(month) ?? 0 }));
  }, [rows, activeCategory, subcategory, timeline]);

  return (
    <section className="overview-trend">
      <h2>Category spend over time</h2>
      <p className="chart-caption">
        Net spend by month for one category. Gaps in the selected window plot as $0. Uses its own timeline so a
        single-month Overview still shows a trend.
      </p>

      {monthly.error && <ErrorNote error={monthly.error} />}
      {monthly.loading && <Loading what="category spend" />}
      {!monthly.loading && rows.length === 0 && <Empty>No categorized spend yet.</Empty>}

      {months.length > 0 && (
        <div className="toolbar overview-controls">
          <PeriodPicker months={months} value={period} onChange={setPeriod} />
          <label>
            Category
            <select
              value={activeCategory}
              onChange={(event) => {
                setCategory(event.target.value);
                setSubcategory("");
              }}
            >
              {categories.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          {activeCategory && (
            <label>
              Subcategory
              <select value={subcategory} onChange={(event) => setSubcategory(event.target.value)}>
                <option value="">All subcategories</option>
                {subcategoryOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}

      {series.length > 0 && (
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={series} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e7dfd2" vertical={false} />
              <XAxis dataKey="month" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} tickFormatter={(value) => compactMoney(Number(value))} />
              <Tooltip formatter={(value) => money(Number(value))} contentStyle={{ fontSize: 13, borderRadius: 4 }} />
              <Line type="monotone" dataKey="total" stroke="#0f5c4c" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

function deltaCaption(data: OverviewMonth): string {
  return data.preset === "month" || data.preset === "prev_month" ? "vs last month" : "vs prior period";
}

function deltaLabel(data: OverviewMonth): string {
  if (data.spend_delta == null) return "No prior period";
  const pct = data.spend_delta_pct == null ? "" : ` (${signedNumber(data.spend_delta_pct)}%)`;
  return `${signedMoney(data.spend_delta)}${pct}`;
}

function signedMoney(value: number | null | undefined): string {
  if (value == null) return "—";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${money(value)}`;
}

function signedNumber(value: number): string {
  return `${value > 0 ? "+" : ""}${value}`;
}

function varianceClass(value: number): string {
  if (value > 0) return "delta-up";
  if (value < 0) return "delta-down";
  return "";
}
