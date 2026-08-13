import { useEffect, useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, MerchantCell, PageHeader } from "../components/ui";
import { api, canWrite } from "../lib/dataSource";
import { cashflow, shortDate } from "../lib/format";
import { replaceHash, useHashLocation } from "../lib/router";
import type { Transaction } from "../lib/types";
import { useAsync } from "../lib/useAsync";

type SortKey = "posted_date" | "amount" | "card" | "merchant" | "category";

function paramsFromHash(params: Record<string, string>): {
  query: string;
  card: string;
  category: string;
  subcategory: string;
  tag: string;
  merchant: string;
  unclassified: boolean;
  since: string;
  until: string;
  sort: SortKey;
  order: "asc" | "desc";
  txn: string;
} {
  const sort = (["posted_date", "amount", "card", "merchant", "category"] as const).includes(
    params.sort as SortKey,
  )
    ? (params.sort as SortKey)
    : "posted_date";
  return {
    query: params.q ?? "",
    card: params.card ?? "",
    category: params.category ?? "",
    subcategory: params.subcategory ?? "",
    tag: params.tag ?? "",
    merchant: params.merchant ?? "",
    unclassified: params.unclassified === "1",
    since: params.since ?? "",
    until: params.until ?? "",
    sort,
    order: params.order === "asc" ? "asc" : "desc",
    txn: params.txn ?? "",
  };
}

export function Transactions() {
  const { params } = useHashLocation();
  const parsed = paramsFromHash(params);
  const [query, setQuery] = useState(parsed.query);
  const [card, setCard] = useState(parsed.card);
  const [category, setCategory] = useState(parsed.category);
  const [subcategory, setSubcategory] = useState(parsed.subcategory);
  const [tag, setTag] = useState(parsed.tag);
  const [merchant, setMerchant] = useState(parsed.merchant);
  const [unclassified, setUnclassified] = useState(parsed.unclassified);
  const [since, setSince] = useState(parsed.since);
  const [until, setUntil] = useState(parsed.until);
  const [sort, setSort] = useState<SortKey>(parsed.sort);
  const [order, setOrder] = useState<"asc" | "desc">(parsed.order);
  const [selectedId, setSelectedId] = useState(parsed.txn);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [bulkCategory, setBulkCategory] = useState("");
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  useEffect(() => {
    const next = paramsFromHash(params);
    setQuery(next.query);
    setCard(next.card);
    setCategory(next.category);
    setSubcategory(next.subcategory);
    setTag(next.tag);
    setMerchant(next.merchant);
    setUnclassified(next.unclassified);
    setSince(next.since);
    setUntil(next.until);
    setSort(next.sort);
    setOrder(next.order);
    setSelectedId(next.txn);
  }, [params]);

  useEffect(() => {
    replaceHash("/transactions", {
      q: query,
      card,
      category,
      subcategory,
      tag,
      merchant,
      unclassified: unclassified ? "1" : "",
      since,
      until,
      sort: sort === "posted_date" ? "" : sort,
      order: sort === "posted_date" && order === "desc" ? "" : order,
      txn: selectedId,
    });
  }, [query, card, category, subcategory, tag, merchant, unclassified, since, until, sort, order, selectedId]);

  const tagsState = useAsync(() => api.tags(), []);
  const rulesState = useAsync(() => (canWrite ? api.rules() : Promise.resolve({ categories: [] as string[], subcategories: {}, rules: [] })), []);

  const { data, loading, error, reload } = useAsync(
    () =>
      api.transactions({
        q: query,
        card,
        category,
        subcategory,
        tag,
        merchant,
        unclassified,
        since,
        until,
        sort,
        order,
        limit: 500,
      }),
    [query, card, category, subcategory, tag, merchant, unclassified, since, until, sort, order],
  );

  const items = data?.items ?? [];
  const tagCatalog = tagsState.data?.items ?? [];
  const categories = rulesState.data?.categories ?? [];
  const subcategoryOptions = useMemo(() => {
    const fromRules = category ? rulesState.data?.subcategories?.[category] ?? [] : [];
    if (subcategory && !fromRules.includes(subcategory)) return [...fromRules, subcategory];
    return fromRules;
  }, [category, rulesState.data?.subcategories, subcategory]);
  const selected = items.find((row) => row.txn_id === selectedId) ?? null;
  const facets = useMemo(() => {
    const cards = new Set<string>();
    const cats = new Set<string>();
    for (const txn of items) {
      cards.add(txn.card);
      cats.add(txn.category ?? "Uncategorized");
    }
    return { cards: [...cards].sort(), categories: [...cats].sort() };
  }, [items]);

  const tagLabel = useMemo(() => {
    const map = new Map(tagCatalog.map((entry) => [entry.id, entry.label]));
    return (id: string) => map.get(id) ?? id;
  }, [tagCatalog]);

  function toggleSort(key: SortKey) {
    if (sort === key) {
      setOrder(order === "asc" ? "desc" : "asc");
      return;
    }
    setSort(key);
    setOrder(key === "posted_date" || key === "amount" ? "desc" : "asc");
  }

  function togglePicked(id: string) {
    setPicked((current) => {
      const next = new Set(current);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function applyBulk() {
    if (!bulkCategory || picked.size === 0) return;
    setBulkBusy(true);
    setBulkError(null);
    try {
      await api.bulkTransactions({ txn_ids: [...picked], category: bulkCategory });
      setPicked(new Set());
      reload();
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : String(err));
    } finally {
      setBulkBusy(false);
    }
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setSelectedId("");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <PageHeader
        title="Transactions"
        lede="Filter, sort, and inspect merchant layers. Tags stay optional context and do not change category totals."
      />

      <div className="toolbar">
        <input
          className="grow"
          type="search"
          placeholder="Search merchant or description…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select value={card} onChange={(event) => setCard(event.target.value)}>
          <option value="">All cards</option>
          {facets.cards.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <select
          value={category}
          onChange={(event) => {
            setCategory(event.target.value);
            setSubcategory("");
          }}
        >
          <option value="">All categories</option>
          {facets.categories.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        {category && (
          <select value={subcategory} onChange={(event) => setSubcategory(event.target.value)}>
            <option value="">All subcategories</option>
            {subcategoryOptions.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        )}
        <select value={tag} onChange={(event) => setTag(event.target.value)}>
          <option value="">All tags</option>
          {tagCatalog.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.label}
            </option>
          ))}
        </select>
        <input type="date" value={since} onChange={(event) => setSince(event.target.value)} aria-label="Since" />
        <input type="date" value={until} onChange={(event) => setUntil(event.target.value)} aria-label="Until" />
        <label className="checkbox">
          <input
            type="checkbox"
            checked={unclassified}
            onChange={(event) => setUnclassified(event.target.checked)}
          />
          Needs review only
        </label>
      </div>

      {canWrite && picked.size > 0 && (
        <div className="toolbar bulk-toolbar">
          <span className="muted">{picked.size} selected</span>
          <select value={bulkCategory} onChange={(event) => setBulkCategory(event.target.value)}>
            <option value="">Set category…</option>
            {categories.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <button className="btn" disabled={!bulkCategory || bulkBusy} onClick={() => void applyBulk()}>
            {bulkBusy ? "Saving…" : "Apply category"}
          </button>
          <button className="btn subtle" onClick={() => setPicked(new Set())}>
            Clear
          </button>
        </div>
      )}
      {bulkError && <ErrorNote error={bulkError} />}

      {error && <ErrorNote error={error} />}
      {loading && <Loading what="transactions" />}

      {!loading && items.length === 0 && <Empty>No transactions match these filters.</Empty>}

      {items.length > 0 && (
        <>
          <p className="muted">
            Showing {items.length} of {data?.total ?? items.length}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {canWrite && (
                    <th>
                      <input
                        type="checkbox"
                        checked={picked.size > 0 && items.every((row) => picked.has(row.txn_id))}
                        onChange={(event) => {
                          setPicked(event.target.checked ? new Set(items.map((row) => row.txn_id)) : new Set());
                        }}
                        aria-label="Select all"
                      />
                    </th>
                  )}
                  <SortHeader label="Date" active={sort === "posted_date"} order={order} onClick={() => toggleSort("posted_date")} />
                  <SortHeader label="Card" active={sort === "card"} order={order} onClick={() => toggleSort("card")} />
                  <SortHeader label="Merchant" active={sort === "merchant"} order={order} onClick={() => toggleSort("merchant")} />
                  <th>Raw description</th>
                  <SortHeader label="Amount" active={sort === "amount"} order={order} onClick={() => toggleSort("amount")} numeric />
                  <SortHeader label="Category" active={sort === "category"} order={order} onClick={() => toggleSort("category")} />
                  <th>Tags</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {items.map((txn) => {
                  const flow = cashflow(txn.amount);
                  return (
                    <tr
                      key={txn.txn_id}
                      className={`clickable-row${selectedId === txn.txn_id ? " selected-row" : ""}`}
                      onClick={() => setSelectedId(txn.txn_id)}
                    >
                      {canWrite && (
                        <td onClick={(event) => event.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={picked.has(txn.txn_id)}
                            onChange={() => togglePicked(txn.txn_id)}
                            aria-label={`Select ${txn.canonical_merchant || txn.normalized_merchant}`}
                          />
                        </td>
                      )}
                      <td>{shortDate(txn.posted_date)}</td>
                      <td>{txn.card}</td>
                      <td>
                        <MerchantCell
                          canonical={txn.canonical_merchant}
                          normalized={txn.normalized_merchant}
                        />
                      </td>
                      <td className="muted">{txn.raw_description}</td>
                      <td className={`num amount-${flow.kind}`}>{flow.text}</td>
                      <td>
                        {txn.category ?? <span className="unresolved">Uncategorized</span>}
                        {txn.subcategory ? <span className="merchant-raw"> {txn.subcategory}</span> : null}
                        {txn.category === "Transfer" ? <span className="tag">Transfer</span> : null}
                      </td>
                      <td>
                        <div className="tag-chip-row compact">
                          {(txn.tags ?? []).map((id) => (
                            <span key={id} className="tag-chip readonly">
                              {tagLabel(id)}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>
                        <span className={`status status-${txn.classified_by ?? "missing"}`}>
                          {txn.classified_by ?? "open"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {selected && (
        <TransactionDrawer
          txn={selected}
          tagLabel={tagLabel}
          onClose={() => setSelectedId("")}
        />
      )}
    </>
  );
}

function SortHeader({
  label,
  active,
  order,
  onClick,
  numeric,
}: {
  label: string;
  active: boolean;
  order: "asc" | "desc";
  onClick: () => void;
  numeric?: boolean;
}) {
  return (
    <th className={`sortable${numeric ? " num" : ""}${active ? " active" : ""}`}>
      <button type="button" onClick={onClick}>
        {label}
        {active ? (order === "asc" ? " ▲" : " ▼") : ""}
      </button>
    </th>
  );
}

function TransactionDrawer({
  txn,
  tagLabel,
  onClose,
}: {
  txn: Transaction;
  tagLabel: (id: string) => string;
  onClose: () => void;
}) {
  const flow = cashflow(txn.amount);
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()} aria-label="Transaction detail">
        <div className="cluster-head">
          <h2>Transaction</h2>
          <button className="btn subtle small" onClick={onClose}>
            Close
          </button>
        </div>
        <p className={`review-amount amount-${flow.kind}`}>{flow.text}</p>
        <dl className="review-fields">
          <dt>Date</dt>
          <dd>{shortDate(txn.posted_date)}</dd>
          <dt>Card</dt>
          <dd>{txn.card}</dd>
          <dt>Raw</dt>
          <dd>{txn.raw_description}</dd>
          <dt>Normalized</dt>
          <dd>{txn.normalized_merchant}</dd>
          <dt>Canonical</dt>
          <dd>{txn.canonical_merchant ?? <span className="unresolved">none</span>}</dd>
          <dt>Category</dt>
          <dd>{[txn.category, txn.subcategory].filter(Boolean).join(" / ") || "Uncategorized"}</dd>
          <dt>Source</dt>
          <dd>{txn.classified_by ?? "open"}</dd>
          <dt>File</dt>
          <dd>{txn.source_file || "—"}</dd>
          <dt>Tags</dt>
          <dd>{(txn.tags ?? []).map(tagLabel).join(", ") || "—"}</dd>
        </dl>
      </aside>
    </div>
  );
}
