import { useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader, StatusPill } from "../components/ui";
import { api } from "../lib/dataSource";
import { money, shortDate } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function Recurring() {
  const recurring = useAsync(() => api.recurring(), []);
  const recon = useAsync(() => api.reconciliation(), []);
  const [onlyRecurring, setOnlyRecurring] = useState(true);

  const rows = (recurring.data ?? []).filter((r) => !onlyRecurring || r.is_recurring);

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
        !recon.loading && <Empty>No expected bills configured in config/expected_recurring.yaml.</Empty>
      )}

      <h2>Detected recurring merchants</h2>
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

      {recurring.loading && <Loading what="recurring merchants" />}
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
                <th>Recurring</th>
                <th className="num">Occurrences</th>
                <th className="num">Average</th>
                <th className="num">Std dev</th>
                <th className="num">Median gap</th>
                <th>Category</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.normalized_merchant}>
                  <td>{row.normalized_merchant}</td>
                  <td>{row.is_recurring ? "yes" : "no"}</td>
                  <td className="num">{row.occurrences}</td>
                  <td className="num">{money(row.avg_amount)}</td>
                  <td className="num">{money(row.std_amount)}</td>
                  <td className="num">
                    {row.median_gap_days === null ? "—" : `${row.median_gap_days}d`}
                  </td>
                  <td>{[row.category, row.subcategory].filter(Boolean).join(" / ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
