import { useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader, StatusPill } from "../components/ui";
import { api } from "../lib/dataSource";
import { money, shortDate } from "../lib/format";
import { hashHref } from "../lib/router";
import type { RecurringRow } from "../lib/types";
import { useAsync } from "../lib/useAsync";

function flagList(flags?: string | string[]): string[] {
  if (Array.isArray(flags)) return flags.filter(Boolean);
  if (typeof flags === "string" && flags.trim()) return flags.split(",").map((item) => item.trim()).filter(Boolean);
  return [];
}

function asOfDate(values: Array<string | null | undefined>): string | null {
  const dates = values.filter((value): value is string => Boolean(value)).sort();
  return dates.at(-1) ?? null;
}

function isStale(lastSeen: string | null | undefined, asOf: string | null): boolean {
  if (!lastSeen || !asOf) return false;
  return (Date.parse(asOf) - Date.parse(lastSeen)) / 86_400_000 > 45;
}

export function Recurring() {
  const recurring = useAsync(() => api.recurring(), []);
  const recon = useAsync(() => api.reconciliation(), []);
  const [onlyRecurring, setOnlyRecurring] = useState(true);

  const rows = useMemo(() => {
    const items = (recurring.data ?? []).filter((row) => {
      if ((row.category ?? "").trim().toLowerCase() !== "subscriptions") return false;
      return !onlyRecurring || row.is_recurring;
    });
    return [...items].sort((left, right) => {
      const leftFlags = flagList(left.flags).length;
      const rightFlags = flagList(right.flags).length;
      if (leftFlags !== rightFlags) return rightFlags - leftFlags;
      return right.avg_amount - left.avg_amount;
    });
  }, [onlyRecurring, recurring.data]);

  const asOf = asOfDate([
    ...(recurring.data ?? []).map((row) => row.last_seen),
    ...(recon.data ?? []).map((row) => row.last_seen),
  ]);

  return (
    <>
      <PageHeader
        title="Recurring bills"
        lede="Merchants charging a roughly constant amount on a monthly cadence, reconciled against your expected bills."
      />

      <h2>Expected bills</h2>
      {recon.loading && <Loading what="reconciliation" />}
      {recon.error && <ErrorNote error={recon.error} />}
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
                <th className="num">Last price</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {recon.data.map((row) => (
                <tr
                  key={row.bill}
                  className={row.matched_merchant ? "clickable-row" : undefined}
                  onClick={() => {
                    if (row.matched_merchant) {
                      window.location.hash = hashHref("/transactions", { merchant: row.matched_merchant });
                    }
                  }}
                >
                  <td>{row.bill}</td>
                  <td>
                    <StatusPill status={row.status} />
                    {isStale(row.last_seen, asOf) && <span className="flag-pill">stale</span>}
                  </td>
                  <td className="num">{money(row.expected_amount)}</td>
                  <td>{row.matched_merchant ?? "—"}</td>
                  <td className="num">{money(row.matched_avg)}</td>
                  <td className="num">{money(row.last_amount)}</td>
                  <td>{shortDate(row.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !recon.loading && <Empty>No expected bills configured in config/expected_recurring.yaml.</Empty>
      )}

      <h2>Subscriptions</h2>
      <div className="toolbar">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={onlyRecurring}
            onChange={(e) => setOnlyRecurring(e.target.checked)}
          />
          Only confirmed recurring
        </label>
      </div>

      {recurring.loading && <Loading what="subscriptions" />}
      {recurring.error && <ErrorNote error={recurring.error} />}
      {!recurring.loading && rows.length === 0 && (
        <Empty>Nothing detected yet. Run build after ingesting a few months of statements.</Empty>
      )}

      {rows.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Merchant</th>
                <th>Flags</th>
                <th className="num">Average spend</th>
                <th className="num">Months</th>
                <th className="num">Last price</th>
                <th>Last seen</th>
                <th>Category</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const merchant = row.canonical_merchant || row.normalized_merchant;
                return (
                <tr
                  key={merchant}
                  className="clickable-row"
                  onClick={() => {
                    window.location.hash = hashHref("/transactions", { merchant });
                  }}
                >
                  <td>{merchant}</td>
                  <td>
                    <FlagPills row={row} />
                  </td>
                  <td className="num">{money(row.avg_amount)}</td>
                  <td className="num">{row.months == null ? "—" : row.months}</td>
                  <td className="num">{money(row.last_amount)}</td>
                  <td>{shortDate(row.last_seen ?? null)}</td>
                  <td>{[row.category, row.subcategory].filter(Boolean).join(" / ") || "—"}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function FlagPills({ row }: { row: RecurringRow }) {
  const flags = flagList(row.flags);
  if (flags.length === 0) return <span className="muted">—</span>;
  return (
    <div className="flag-row">
      {flags.includes("price_hike") && (
        <span className="flag-pill">
          price hike
          {row.avg_amount != null && row.last_amount != null
            ? ` ${money(row.avg_amount)} → ${money(row.last_amount)}`
            : ""}
        </span>
      )}
      {flags.includes("stale") && <span className="flag-pill">stale</span>}
    </div>
  );
}
