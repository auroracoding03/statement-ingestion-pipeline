import { useEffect, useMemo, useState } from "react";

import { CategoryFields } from "../components/CategoryFields";
import { Empty, ErrorNote, Loading, MerchantCell, PageHeader } from "../components/ui";
import { api, canWrite } from "../lib/dataSource";
import { cashflow, shortDate } from "../lib/format";
import { sortedLabels } from "../lib/sort";
import { replaceHash, useHashLocation } from "../lib/router";
import type { ContextTag, Transaction } from "../lib/types";
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
  const [bulkSubcategory, setBulkSubcategory] = useState("");
  const [bulkTags, setBulkTags] = useState<string[]>([]);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [debouncedQuery, setDebouncedQuery] = useState(query);

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQuery(query), 250);
    return () => window.clearTimeout(id);
  }, [query]);

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
        q: debouncedQuery,
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
    [debouncedQuery, card, category, subcategory, tag, merchant, unclassified, since, until, sort, order],
  );

  const items = data?.items ?? [];
  const tagCatalog = tagsState.data?.items ?? [];
  const categories = rulesState.data?.categories ?? [];
  const subcategoryOptions = useMemo(() => {
    const fromRules = category ? rulesState.data?.subcategories?.[category] ?? [] : [];
    const options = subcategory && !fromRules.includes(subcategory) ? [...fromRules, subcategory] : fromRules;
    return sortedLabels(options);
  }, [category, rulesState.data?.subcategories, subcategory]);
  const selected = items.find((row) => row.txn_id === selectedId) ?? null;
  const facets = useMemo(() => {
    const cards = new Set<string>();
    const cats = new Set<string>();
    for (const txn of items) {
      cards.add(txn.card);
      cats.add(txn.category ?? "Uncategorized");
    }
    return { cards: sortedLabels(cards), categories: sortedLabels(cats) };
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
    if (picked.size === 0) return;
    if (!bulkCategory && bulkTags.length === 0) return;
    setBulkBusy(true);
    setBulkError(null);
    try {
      await api.bulkTransactions({
        txn_ids: [...picked],
        ...(bulkCategory ? { category: bulkCategory, subcategory: bulkSubcategory } : {}),
        ...(bulkTags.length ? { add_tags: bulkTags } : {}),
      });
      setPicked(new Set());
      setBulkCategory("");
      setBulkSubcategory("");
      setBulkTags([]);
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
          <div className="bulk-fields">
            <CategoryFields
              categories={categories}
              subcategories={rulesState.data?.subcategories ?? {}}
              category={bulkCategory}
              subcategory={bulkSubcategory}
              categoryLabel="Set category…"
              subcategoryLabel="Set subcategory…"
              onCategoryChange={(nextCategory, nextSub) => {
                setBulkCategory(nextCategory);
                setBulkSubcategory(nextSub);
              }}
              onPairChange={(nextCategory, nextSub) => {
                setBulkCategory(nextCategory);
                setBulkSubcategory(nextSub);
              }}
            />
            <select
              value=""
              aria-label="Add tag"
              onChange={(event) => {
                const id = event.target.value;
                if (id && !bulkTags.includes(id)) setBulkTags([...bulkTags, id]);
              }}
            >
              <option value="">Add tag…</option>
              {tagCatalog
                .filter((entry) => !bulkTags.includes(entry.id))
                .map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.label}
                  </option>
                ))}
            </select>
            {bulkTags.length > 0 && (
              <div className="tag-chip-row compact">
                {bulkTags.map((id) => (
                  <button
                    key={id}
                    type="button"
                    className="tag-chip active"
                    onClick={() => setBulkTags(bulkTags.filter((tag) => tag !== id))}
                  >
                    {tagLabel(id)}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            className="btn"
            disabled={(!bulkCategory && bulkTags.length === 0) || bulkBusy}
            onClick={() => void applyBulk()}
          >
            {bulkBusy ? "Saving…" : "Apply Selection(s)"}
          </button>
          <button
            className="btn subtle"
            onClick={() => {
              setPicked(new Set());
              setBulkCategory("");
              setBulkSubcategory("");
              setBulkTags([]);
            }}
          >
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
          tagCatalog={tagCatalog}
          categories={categories}
          subcategories={rulesState.data?.subcategories ?? {}}
          onClose={() => setSelectedId("")}
          onUpdated={() => reload()}
          onDeleted={(txnId) => {
            setSelectedId("");
            setPicked((current) => {
              if (!current.has(txnId)) return current;
              const next = new Set(current);
              next.delete(txnId);
              return next;
            });
            reload();
          }}
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
  tagCatalog,
  categories,
  subcategories,
  onClose,
  onUpdated,
  onDeleted,
}: {
  txn: Transaction;
  tagLabel: (id: string) => string;
  tagCatalog: ContextTag[];
  categories: string[];
  subcategories: Record<string, string[]>;
  onClose: () => void;
  onUpdated: () => void;
  onDeleted: (txnId: string) => void;
}) {
  const flow = cashflow(txn.amount);
  const [confirming, setConfirming] = useState(false);
  const [armed, setArmed] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [category, setCategory] = useState(txn.category ?? "");
  const [subcategory, setSubcategory] = useState(txn.subcategory ?? "");
  const [tags, setTags] = useState<string[]>(txn.tags ?? []);
  const merchant = txn.canonical_merchant || txn.normalized_merchant || txn.raw_description;

  useEffect(() => {
    setCategory(txn.category ?? "");
    setSubcategory(txn.subcategory ?? "");
    setTags(txn.tags ?? []);
    setError("");
  }, [txn.txn_id, txn.category, txn.subcategory, txn.tags]);

  useEffect(() => {
    if (!confirming) {
      setArmed(false);
      return;
    }
    const id = window.setTimeout(() => setArmed(true), 0);
    return () => window.clearTimeout(id);
  }, [confirming]);

  async function saveCategory(nextCategory: string, nextSub: string) {
    setCategory(nextCategory);
    setSubcategory(nextSub);
    if (!canWrite || !nextCategory) return;
    if (nextCategory === (txn.category ?? "") && nextSub === (txn.subcategory ?? "")) return;
    setSaving(true);
    setError("");
    try {
      await api.bulkTransactions({
        txn_ids: [txn.txn_id],
        category: nextCategory,
        subcategory: nextSub,
      });
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save category.");
    } finally {
      setSaving(false);
    }
  }

  async function saveTags(nextTags: string[]) {
    setTags(nextTags);
    if (!canWrite) return;
    setSaving(true);
    setError("");
    try {
      await api.bulkTransactions({ txn_ids: [txn.txn_id], tags: nextTags });
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save tags.");
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    setDeleting(true);
    setError("");
    try {
      await api.deleteTransaction(txn.txn_id);
      onDeleted(txn.txn_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete this transaction.");
      setDeleting(false);
    }
  }

  const unusedTags = tagCatalog.filter((entry) => !tags.includes(entry.id));

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose}>
        <aside className="drawer" onClick={(event) => event.stopPropagation()} aria-label="Transaction detail">
          <div className="cluster-head">
            <h2>Transaction</h2>
            <button className="btn subtle small" onClick={onClose}>
              Close
            </button>
          </div>
          <p className={`review-amount amount-${flow.kind}`}>{flow.text}</p>
          {error && !confirming && <ErrorNote error={error} />}
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
            <dd>
              {canWrite ? (
                <div className="drawer-edit">
                  <CategoryFields
                    categories={categories}
                    subcategories={subcategories}
                    category={category}
                    subcategory={subcategory}
                    requiredCategory
                    categoryLabel="Category"
                    subcategoryLabel="Subcategory"
                    onCategoryChange={(nextCategory, nextSub) => {
                      void saveCategory(nextCategory, nextSub);
                    }}
                    onPairChange={(nextCategory, nextSub) => {
                      void saveCategory(nextCategory, nextSub);
                    }}
                  />
                </div>
              ) : (
                [txn.category, txn.subcategory].filter(Boolean).join(" / ") || "Uncategorized"
              )}
            </dd>
            <dt>Source</dt>
            <dd>{txn.classified_by ?? "open"}</dd>
            <dt>File</dt>
            <dd>{txn.source_file || "—"}</dd>
            <dt>Tags</dt>
            <dd>
              {canWrite ? (
                <div className="drawer-edit">
                  <select
                    value=""
                    disabled={saving || unusedTags.length === 0}
                    aria-label="Add tag"
                    onChange={(event) => {
                      const id = event.target.value;
                      if (id) void saveTags([...tags, id]);
                    }}
                  >
                    <option value="">Add tag…</option>
                    {unusedTags.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.label}
                      </option>
                    ))}
                  </select>
                  {tags.length > 0 ? (
                    <div className="tag-chip-row compact">
                      {tags.map((id) => (
                        <button
                          key={id}
                          type="button"
                          className="tag-chip active"
                          disabled={saving}
                          onClick={() => void saveTags(tags.filter((tag) => tag !== id))}
                        >
                          {tagLabel(id)}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <span className="muted">None</span>
                  )}
                </div>
              ) : (
                (txn.tags ?? []).map(tagLabel).join(", ") || "—"
              )}
            </dd>
          </dl>
          {canWrite && (
            <div className="review-actions">
              <button className="btn danger" type="button" onClick={() => setConfirming(true)}>
                Delete
              </button>
            </div>
          )}
        </aside>
      </div>
      {confirming && (
        <div
          className={`modal-backdrop${armed ? "" : " is-pending"}`}
          onClick={(event) => {
            if (!armed || deleting || event.target !== event.currentTarget) return;
            setConfirming(false);
          }}
          role="presentation"
        >
          <div
            className="upload-modal"
            role="dialog"
            aria-labelledby="txn-delete-title"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 id="txn-delete-title">Delete transaction</h2>
            <p>
              Remove <strong>{merchant}</strong> on {shortDate(txn.posted_date)} ({flow.text}) from the
              ledger? Re-ingesting the same statement will not restore it.
            </p>
            {error && <ErrorNote error={error} />}
            <div className="review-actions">
              <button className="btn danger" type="button" onClick={() => void confirmDelete()} disabled={deleting}>
                {deleting ? "Deleting…" : "Delete transaction"}
              </button>
              <button
                className="btn subtle"
                type="button"
                onClick={() => setConfirming(false)}
                disabled={deleting}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
