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

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { compactMoney, money } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function Categories() {
  const { data, loading, error } = useAsync(() => api.categoriesMonthly(), []);
  const rows = data ?? [];
  const [selected, setSelected] = useState<string>("");

  const categories = useMemo(
    () => [...new Set(rows.map((r) => r.category))].sort(),
    [rows],
  );

  const totals = useMemo(() => {
    const map = new Map<string, { total: number; txn_count: number }>();
    for (const r of rows) {
      const prev = map.get(r.category) ?? { total: 0, txn_count: 0 };
      map.set(r.category, {
        total: prev.total + r.total,
        txn_count: prev.txn_count + r.txn_count,
      });
    }
    return [...map.entries()].sort((a, b) => b[1].total - a[1].total);
  }, [rows]);

  const trend = useMemo(() => {
    const active = selected || totals[0]?.[0];
    if (!active) return { active: "", data: [] };
    const byMonth = new Map<string, number>();
    for (const r of rows.filter((r) => r.category === active)) {
      byMonth.set(r.month, (byMonth.get(r.month) ?? 0) + r.total);
    }
    return {
      active,
      data: [...byMonth.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([month, total]) => ({ month, total })),
    };
  }, [rows, selected, totals]);

  return (
    <>
      <PageHeader title="Spend by category" lede="Monthly totals derived from the canonical ledger." />

      {error && <ErrorNote error={error} />}
      {loading && <Loading what="categories" />}
      {!loading && rows.length === 0 && <Empty>No categorized spend yet.</Empty>}

      {trend.data.length > 0 && (
        <>
          <div className="toolbar">
            <label className="muted" htmlFor="cat-select">
              Trend for
            </label>
            <select
              id="cat-select"
              value={trend.active}
              onChange={(e) => setSelected(e.target.value)}
            >
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={trend.data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e7dfd2" vertical={false} />
                <XAxis dataKey="month" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => compactMoney(Number(v))} />
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
                  <tr key={category}>
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
