import { useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, Metric, PageHeader, SortHeader, StatusPill } from "../components/ui";
import { api, canWrite } from "../lib/dataSource";
import { money, shortDate } from "../lib/format";
import {
  compareNumber,
  compareText,
  compareWithSecondary,
  nextColumnSort,
  type ColumnSort,
} from "../lib/sort";
import type { CardProductCoverage } from "../lib/types";
import { useAsync } from "../lib/useAsync";

type AccountSortKey =
  | "product"
  | "cardholder"
  | "status"
  | "statements"
  | "coverage"
  | "gross"
  | "returns"
  | "net"
  | "income"
  | "expenses"
  | "last_statement";

const ACCOUNT_NUMERIC_SORT = new Set<AccountSortKey>([
  "statements",
  "coverage",
  "gross",
  "returns",
  "net",
  "income",
  "expenses",
  "last_statement",
]);

const STATUS_RANK: Record<string, number> = { gap: 0, stale: 1, ok: 2, none: 3 };

function productLabel(row: CardProductCoverage): string {
  return `${row.issuer} ${row.product}`.trim() || row.label;
}

function dateValue(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function compareAccounts(
  left: CardProductCoverage,
  right: CardProductCoverage,
  key: AccountSortKey,
  order: ColumnSort<AccountSortKey>["order"],
): number {
  if (key === "product") return compareText(productLabel(left), productLabel(right), order);
  if (key === "cardholder") return compareText(left.cardholder ?? "", right.cardholder ?? "", order);
  if (key === "status") {
    return compareNumber(STATUS_RANK[left.status] ?? 99, STATUS_RANK[right.status] ?? 99, order);
  }
  if (key === "statements") return compareNumber(left.statement_count, right.statement_count, order);
  if (key === "coverage") {
    return compareWithSecondary(
      compareNumber(dateValue(left.coverage_start), dateValue(right.coverage_start), order),
      compareNumber(dateValue(left.coverage_end), dateValue(right.coverage_end), order),
    );
  }
  if (key === "gross") return compareNumber(left.gross_charges ?? left.spend_total, right.gross_charges ?? right.spend_total, order);
  if (key === "returns") return compareNumber(left.returns_total ?? 0, right.returns_total ?? 0, order);
  if (key === "net") return compareNumber(left.spend_total, right.spend_total, order);
  if (key === "income") return compareNumber(left.income_total ?? 0, right.income_total ?? 0, order);
  if (key === "expenses") return compareNumber(left.bank_expenses ?? 0, right.bank_expenses ?? 0, order);
  return compareNumber(dateValue(left.coverage_end), dateValue(right.coverage_end), order);
}

function sortAccounts(rows: CardProductCoverage[], sort: ColumnSort<AccountSortKey>): CardProductCoverage[] {
  return [...rows].sort((left, right) => {
    const primary = compareAccounts(left, right, sort.key, sort.order);
    if (!sort.secondaryKey) return primary;
    return compareWithSecondary(
      primary,
      compareAccounts(left, right, sort.secondaryKey, sort.secondaryOrder),
    );
  });
}

export function Cards() {
  const coverage = useAsync(() => api.cards(), []);
  const status = useAsync(() => api.status(), []);
  const [selectedKey, setSelectedKey] = useState("");
  const [sort, setSort] = useState<ColumnSort<AccountSortKey>>({
    key: "product",
    order: "asc",
    secondaryKey: null,
    secondaryOrder: "asc",
  });
  const [removeError, setRemoveError] = useState("");
  const products = coverage.data?.products ?? [];
  const selected = useMemo(
    () => products.find((row) => productKey(row) === selectedKey) ?? null,
    [products, selectedKey],
  );
  const cards = useMemo(
    () => sortAccounts(products.filter((row) => (row.account_kind ?? "card") !== "bank"), sort),
    [products, sort],
  );
  const banks = useMemo(
    () => sortAccounts(products.filter((row) => row.account_kind === "bank"), sort),
    [products, sort],
  );
  const holders = status.data?.cardholders ?? [];

  function toggleSort(key: AccountSortKey) {
    setSort((current) => nextColumnSort(current, key, ACCOUNT_NUMERIC_SORT));
  }

  async function removeProduct(row: CardProductCoverage) {
    const label = productLabel(row);
    if (
      !window.confirm(
        `Remove ${label} from the product list? This does not delete any transactions.`,
      )
    ) {
      return;
    }
    setRemoveError("");
    try {
      await api.removeCardProduct(row.issuer, row.product);
      coverage.reload({ silent: true });
    } catch (err) {
      setRemoveError(err instanceof Error ? err.message : "Could not remove that product.");
    }
  }

  return (
    <>
      <PageHeader
        title="Accounts"
        lede="See which statements you have for each credit product and bank account. Cards show net spend after merchant returns; bank rows show income and true expenses, not transfers."
      />

      {coverage.error && <ErrorNote error={coverage.error} />}
      {removeError && <ErrorNote error={removeError} />}
      {coverage.loading && <Loading what="account coverage" />}

      {coverage.data && !coverage.loading && (
        <>
          <div className="toolbar overview-controls">
            <label>
              Account
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
            <ProductDetail
              key={productKey(selected)}
              product={selected}
              holders={holders}
              onAssigned={(name) => {
                setSelectedKey(`${selected.issuer}||${selected.product}||${name}`);
                coverage.reload({ silent: true });
                status.reload({ silent: true });
              }}
            />
          ) : (
            <>
              <h2>Cards</h2>
              {cards.length > 0 ? (
                <ProductTable
                  products={cards}
                  sort={sort}
                  onSort={toggleSort}
                  onSelect={setSelectedKey}
                  onRemove={removeProduct}
                  variant="card"
                />
              ) : (
                <Empty>No card products on the ledger yet. Ingest a statement to get started.</Empty>
              )}
              <h2>Bank accounts</h2>
              {banks.length > 0 ? (
                <ProductTable
                  products={banks}
                  sort={sort}
                  onSort={toggleSort}
                  onSelect={setSelectedKey}
                  onRemove={removeProduct}
                  variant="bank"
                />
              ) : (
                <Empty>No bank or debit accounts in the ledger yet.</Empty>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}

function ProductTable({
  products,
  sort,
  onSort,
  onSelect,
  onRemove,
  variant = "card",
}: {
  products: CardProductCoverage[];
  sort: ColumnSort<AccountSortKey>;
  onSort: (key: AccountSortKey) => void;
  onSelect: (key: string) => void;
  onRemove: (row: CardProductCoverage) => void;
  variant?: "card" | "bank";
}) {
  function rank(key: AccountSortKey): 1 | 2 | undefined {
    if (sort.key === key) return 1;
    if (sort.secondaryKey === key) return 2;
    return undefined;
  }
  function order(key: AccountSortKey) {
    return sort.key === key ? sort.order : sort.secondaryOrder;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <SortHeader label="Product" rank={rank("product")} order={order("product")} onClick={() => onSort("product")} />
            <SortHeader label="Cardholder" rank={rank("cardholder")} order={order("cardholder")} onClick={() => onSort("cardholder")} />
            <SortHeader label="Status" rank={rank("status")} order={order("status")} onClick={() => onSort("status")} />
            <SortHeader
              label="Statements"
              rank={rank("statements")}
              order={order("statements")}
              onClick={() => onSort("statements")}
              numeric
            />
            <SortHeader label="Coverage" rank={rank("coverage")} order={order("coverage")} onClick={() => onSort("coverage")} />
            {variant === "bank" ? (
              <>
                <SortHeader label="Income" rank={rank("income")} order={order("income")} onClick={() => onSort("income")} numeric />
                <SortHeader label="Bank expenses" rank={rank("expenses")} order={order("expenses")} onClick={() => onSort("expenses")} numeric />
              </>
            ) : (
              <>
                <SortHeader label="Gross" rank={rank("gross")} order={order("gross")} onClick={() => onSort("gross")} numeric />
                <SortHeader label="Returns" rank={rank("returns")} order={order("returns")} onClick={() => onSort("returns")} numeric />
                <SortHeader label="Net" rank={rank("net")} order={order("net")} onClick={() => onSort("net")} numeric />
              </>
            )}
            <SortHeader
              label="Last statement"
              rank={rank("last_statement")}
              order={order("last_statement")}
              onClick={() => onSort("last_statement")}
            />
            {canWrite && <th className="actions-cell">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {products.map((row) => (
            <tr
              key={productKey(row)}
              className="clickable-row"
              onClick={() => onSelect(productKey(row))}
            >
              <td>{productLabel(row)}</td>
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
              {variant === "bank" ? (
                <>
                  <td className="num">{money(row.income_total ?? 0)}</td>
                  <td className="num">{money(row.bank_expenses ?? 0)}</td>
                </>
              ) : (
                <>
                  <td className="num">{money(row.gross_charges ?? row.spend_total)}</td>
                  <td className="num">{money(row.returns_total ?? 0)}</td>
                  <td className="num">{money(row.spend_total)}</td>
                </>
              )}
              <td>{shortDate(row.coverage_end)}</td>
              {canWrite && (
                <td className="actions-cell">
                  {row.statement_count === 0 ? (
                    <button
                      className="btn danger small"
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onRemove(row);
                      }}
                    >
                      Remove
                    </button>
                  ) : null}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProductDetail({
  product,
  holders,
  onAssigned,
}: {
  product: CardProductCoverage;
  holders: string[];
  onAssigned: (cardholder: string) => void;
}) {
  const [holder, setHolder] = useState("");
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const name = custom.trim() || holder;

  async function assign() {
    if (!name) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.assignCardholder(product.issuer, product.product, name);
      onAssigned(result.cardholder);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not assign a cardholder.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {canWrite && product.cardholder === "Unassigned" && (
        <div className="toolbar overview-controls">
          <label>
            Assign cardholder
            <select value={holder} onChange={(event) => setHolder(event.target.value)} disabled={busy}>
              <option value="">Select cardholder</option>
              {holders.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <input
            type="text"
            placeholder="New cardholder (e.g. Alex Example)"
            value={custom}
            onChange={(event) => setCustom(event.target.value)}
            disabled={busy}
          />
          <button className="btn" type="button" disabled={busy || !name} onClick={() => void assign()}>
            {busy ? "Assigning…" : "Assign"}
          </button>
        </div>
      )}
      {error && <ErrorNote error={error} />}

      <div className="metrics">
        <Metric label="Statements" value={product.statement_count} />
        {product.account_kind === "bank" ? (
          <>
            <Metric label="Income" value={money(product.income_total ?? 0)} />
            <Metric label="Bank expenses" value={money(product.bank_expenses ?? 0)} />
          </>
        ) : (
          <>
            <Metric label="Charges" value={product.charge_count} />
            <Metric label="Gross" value={money(product.gross_charges ?? product.spend_total)} />
            <Metric label="Returns" value={money(product.returns_total ?? 0)} />
            <Metric label="Monthly payments" value={money(product.payments_total ?? 0)} />
            <Metric label="Net" value={money(product.spend_total)} />
          </>
        )}
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
                {product.account_kind === "bank" ? (
                  <>
                    <th className="num">Income</th>
                    <th className="num">Bank expenses</th>
                  </>
                ) : (
                  <>
                    <th className="num">Gross</th>
                    <th className="num">Returns</th>
                    <th className="num">Net</th>
                  </>
                )}
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
                  {product.account_kind === "bank" ? (
                    <>
                      <td className="num">{money(row.income_total ?? 0)}</td>
                      <td className="num">{money(row.bank_expenses ?? 0)}</td>
                    </>
                  ) : (
                    <>
                      <td className="num">{money(row.gross_charges ?? row.spend_total)}</td>
                      <td className="num">{money(row.returns_total ?? 0)}</td>
                      <td className="num">{money(row.spend_total)}</td>
                    </>
                  )}
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
