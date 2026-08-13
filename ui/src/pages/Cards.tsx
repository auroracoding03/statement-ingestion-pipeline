import { useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, Metric, PageHeader, StatusPill } from "../components/ui";
import { api } from "../lib/dataSource";
import { money, shortDate } from "../lib/format";
import type { CardProductCoverage } from "../lib/types";
import { useAsync } from "../lib/useAsync";

export function Cards() {
  const coverage = useAsync(() => api.cards(), []);
  const [selectedKey, setSelectedKey] = useState("");
  const products = coverage.data?.products ?? [];
  const selected = useMemo(
    () => products.find((row) => productKey(row) === selectedKey) ?? null,
    [products, selectedKey],
  );

  return (
    <>
      <PageHeader
        title="Cards"
        lede="See which statements you have for each credit product, where coverage looks thin, and how much that card spent."
      />

      {coverage.error && <ErrorNote error={coverage.error} />}
      {coverage.loading && <Loading what="card coverage" />}

      {coverage.data && !coverage.loading && (
        <>
          <div className="toolbar overview-controls">
            <label>
              Credit product
              <select value={selectedKey} onChange={(event) => setSelectedKey(event.target.value)}>
                <option value="">All</option>
                {products.map((row) => (
                  <option key={productKey(row)} value={productKey(row)}>
                    {row.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {selected ? (
            <ProductDetail product={selected} />
          ) : products.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Cardholder</th>
                    <th>Status</th>
                    <th className="num">Statements</th>
                    <th>Coverage</th>
                    <th className="num">Spend</th>
                    <th>Last statement</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((row) => (
                    <tr
                      key={productKey(row)}
                      className="clickable-row"
                      onClick={() => setSelectedKey(productKey(row))}
                    >
                      <td>{`${row.issuer} ${row.product}`.trim() || row.label}</td>
                      <td>{row.cardholder ?? "—"}</td>
                      <td>
                        <StatusPill status={row.status} />
                      </td>
                      <td className="num">{row.statement_count}</td>
                      <td>
                        {row.coverage_start
                          ? `${shortDate(row.coverage_start)} – ${shortDate(row.coverage_end)}`
                          : "—"}
                      </td>
                      <td className="num">{money(row.spend_total)}</td>
                      <td>{shortDate(row.coverage_end)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>No card products on the ledger yet. Ingest a statement to get started.</Empty>
          )}
        </>
      )}
    </>
  );
}

function ProductDetail({ product }: { product: CardProductCoverage }) {
  return (
    <>
      <div className="metrics">
        <Metric label="Statements" value={product.statement_count} />
        <Metric label="Charges" value={product.charge_count} />
        <Metric label="Spend" value={money(product.spend_total)} />
        <Metric label="Payments & refunds" value={money(product.payments_and_refunds)} />
        <Metric
          label="Uncategorized"
          value={`${money(product.uncategorized_total)} · ${product.uncategorized_count}`}
        />
        <Metric
          label="First – last posted"
          value={
            product.first_posted
              ? `${shortDate(product.first_posted)} – ${shortDate(product.last_posted)}`
              : "—"
          }
        />
      </div>

      {product.gaps.length > 0 && (
        <>
          <h2>Missing windows</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>After</th>
                  <th>Before</th>
                  <th className="num">Days</th>
                </tr>
              </thead>
              <tbody>
                {product.gaps.map((gap) => (
                  <tr key={`${gap.after}-${gap.before}`}>
                    <td>{shortDate(gap.after)}</td>
                    <td>{shortDate(gap.before)}</td>
                    <td className="num">{gap.days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {product.stale_days != null && (
        <p className="muted">
          No statement since {shortDate(product.coverage_end)} ({product.stale_days} days).
        </p>
      )}

      <h2>Statements</h2>
      {product.statements.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Coverage</th>
                <th className="num">Transactions</th>
                <th className="num">Spend</th>
              </tr>
            </thead>
            <tbody>
              {product.statements.map((row) => (
                <tr key={row.id}>
                  <td>{row.file_name}</td>
                  <td>
                    {row.coverage_start
                      ? `${shortDate(row.coverage_start)} – ${shortDate(row.coverage_end)}`
                      : "—"}
                  </td>
                  <td className="num">{row.txn_count}</td>
                  <td className="num">{money(row.spend_total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No statements ingested for this product yet.</Empty>
      )}
    </>
  );
}

function productKey(row: CardProductCoverage): string {
  return `${row.issuer}||${row.product}||${row.cardholder ?? ""}`;
}
