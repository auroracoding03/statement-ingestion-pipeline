import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ErrorNote, Loading, Metric, PageHeader, StatusPill } from "../components/ui";
import { api, canWrite } from "../lib/dataSource";
import { compactMoney, money, shortDate } from "../lib/format";
import { hashHref } from "../lib/router";
import type { OverviewMonth } from "../lib/types";
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
  const [month, setMonth] = useState("");
  const [cardholder, setCardholder] = useState("");
  const summary = useAsync(
    () => api.overviewMonth({ month: month || undefined, cardholder: cardholder || undefined }),
    [month, cardholder],
  );

  const data = summary.data;
  const months = data?.months ?? [];
  const selectedMonth = month || data?.month || "";
  const holders = status.data?.cardholders ?? [];
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

  return (
    <>
      <PageHeader
        title="Monthly finance overview"
        lede="Pick a month for a household spend conversation: what changed, what was a one-off, and which bills posted."
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
        <label>
          Month
          <select value={selectedMonth} onChange={(event) => setMonth(event.target.value)} disabled={!months.length}>
            {months.length === 0 && <option value="">No months yet</option>}
            {[...months].reverse().map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
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
      {summary.loading && <Loading what="month summary" />}

      {data && !summary.loading && (
        <>
          <div className="metrics">
            <Metric label="Spend this month" value={money(data.spend_total)} />
            <Metric label="vs last month" value={deltaLabel(data)} />
            <Metric label="Charges" value={data.charge_count} />
            <Metric label="Payments & refunds" value={money(data.payments_and_refunds)} />
            <Metric
              label="Uncategorized"
              value={`${money(data.uncategorized_total)} · ${data.uncategorized_count}`}
            />
            <Metric
              label="Needs review this month"
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

          <h2>Spend by category</h2>
          {chartRows.length > 0 ? (
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height={Math.max(280, chartRows.length * 28)}>
                <BarChart data={chartRows} layout="vertical" margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e7dfd2" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 12 }} tickFormatter={(value) => compactMoney(Number(value))} />
                  <YAxis type="category" dataKey="category" width={120} tick={{ fontSize: 12 }} />
                  <Tooltip
                    formatter={(value) => [money(Number(value)), "Spend"]}
                    contentStyle={{ fontSize: 13, borderRadius: 4 }}
                  />
                  <Bar dataKey="total" fill={COLORS[0]} radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="muted">No categorized spend for this month{cardholder ? ` for ${cardholder}` : ""}.</p>
          )}

          <div className="overview-grid">
            {movers.length > 0 && (
              <section>
                <h2>What changed</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Category</th>
                        <th className="num">This month</th>
                        <th className="num">Change</th>
                      </tr>
                    </thead>
                    <tbody>
                      {movers.map((row) => (
                        <tr key={row.category}>
                          <td>{row.category}</td>
                          <td className="num">{money(row.total)}</td>
                          <td className={`num ${deltaClass(row.delta)}`}>{signedMoney(row.delta)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

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
                        <tr key={`${row.posted_date}-${row.merchant}-${row.amount}`}>
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
                        <tr key={row.id}>
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
        </>
      )}
    </>
  );
}

function deltaLabel(data: OverviewMonth): string {
  if (data.spend_delta == null) return "No prior month";
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

function deltaClass(value: number | null | undefined): string {
  if (value == null || value === 0) return "";
  return value > 0 ? "delta-up" : "delta-down";
}
