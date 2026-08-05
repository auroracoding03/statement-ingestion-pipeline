import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ErrorNote, Loading, Metric, PageHeader, StatusPill } from "../components/ui";
import { api, canWrite } from "../lib/dataSource";
import { compactMoney, money, shortDate } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function Overview() {
  const status = useAsync(() => api.status(), []);
  const recon = useAsync(() => api.reconciliation(), []);
  const monthly = useAsync(() => api.categoriesMonthly(), []);

  const chart = buildChartData(monthly.data ?? []);

  return (
    <>
      <PageHeader
        title="Monthly finance overview"
        lede="Recurring bills, category spend, and how much of the ledger has a resolved merchant identity."
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
                <Link to="/review">{status.data.review_pending}</Link>
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

      <h2>Spend by category</h2>
      {monthly.loading && <Loading what="categories" />}
      {chart.data.length > 0 && (
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={chart.data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e7dfd2" vertical={false} />
              <XAxis dataKey="month" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => compactMoney(Number(v))} />
              <Tooltip
                formatter={(value: number, name: string) => [money(value), name]}
                contentStyle={{ fontSize: 13, borderRadius: 4 }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {chart.categories.map((category, i) => (
                <Bar key={category} dataKey={category} stackId="spend" fill={COLORS[i % COLORS.length]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      {!monthly.loading && chart.data.length === 0 && (
        <p className="muted">No categorized spend yet. Run ingest and classify to populate this.</p>
      )}

      <h2>Expected bill reconciliation</h2>
      {recon.loading && <Loading what="reconciliation" />}
      {recon.data && recon.data.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Bill</th>
                <th>Status</th>
                <th className="num">Expected</th>
                <th>Matched merchant</th>
                <th className="num">Average</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {recon.data.map((row) => (
                <tr key={row.bill}>
                  <td>{row.bill}</td>
                  <td>
                    <StatusPill status={row.status} />
                  </td>
                  <td className="num">{money(row.expected_amount)}</td>
                  <td>{row.matched_merchant ?? "—"}</td>
                  <td className="num">{money(row.matched_avg)}</td>
                  <td>{shortDate(row.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !recon.loading && <p className="muted">No expected bills configured yet.</p>
      )}
    </>
  );
}

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

/** Pivot long-form monthly rows into one record per month with a key per category. */
function buildChartData(rows: { month: string; category: string; total: number }[]) {
  const months = new Map<string, Record<string, number | string>>();
  const totals = new Map<string, number>();

  for (const row of rows) {
    totals.set(row.category, (totals.get(row.category) ?? 0) + row.total);
    const entry = months.get(row.month) ?? { month: row.month };
    entry[row.category] = Number(((entry[row.category] as number) ?? 0) + row.total);
    months.set(row.month, entry);
  }

  // Cap the legend at the biggest categories so the stack stays readable
  const categories = [...totals.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([name]) => name);

  const data = [...months.values()].sort((a, b) => String(a.month).localeCompare(String(b.month)));
  return { data, categories };
}
