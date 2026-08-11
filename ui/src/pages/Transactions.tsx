import { useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, MerchantCell, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { money, shortDate } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function Transactions() {
  const [query, setQuery] = useState("");
  const [card, setCard] = useState("");
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [unclassified, setUnclassified] = useState(false);
  const tagsState = useAsync(() => api.tags(), []);

  const { data, loading, error } = useAsync(
    () => api.transactions({ q: query, card, category, tag, unclassified, limit: 500 }),
    [query, card, category, tag, unclassified],
  );

  const items = data?.items ?? [];
  const tagCatalog = tagsState.data?.items ?? [];
  const facets = useMemo(() => {
    const cards = new Set<string>();
    const categories = new Set<string>();
    for (const t of items) {
      cards.add(t.card);
      categories.add(t.category ?? "Uncategorized");
    }
    return { cards: [...cards].sort(), categories: [...categories].sort() };
  }, [items]);

  const tagLabel = useMemo(() => {
    const map = new Map(tagCatalog.map((entry) => [entry.id, entry.label]));
    return (id: string) => map.get(id) ?? id;
  }, [tagCatalog]);

  return (
    <>
      <PageHeader
        title="Transactions"
        lede="Every row shows merchant layers plus optional context tags (dates, trips) without changing category totals."
      />

      <div className="toolbar">
        <input
          className="grow"
          type="search"
          placeholder="Search merchant or description…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select value={card} onChange={(e) => setCard(e.target.value)}>
          <option value="">All cards</option>
          {facets.cards.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {facets.categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">All tags</option>
          {tagCatalog.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.label}
            </option>
          ))}
        </select>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={unclassified}
            onChange={(e) => setUnclassified(e.target.checked)}
          />
          Needs review only
        </label>
      </div>

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
                  <th>Date</th>
                  <th>Card</th>
                  <th>Merchant</th>
                  <th>Raw description</th>
                  <th className="num">Amount</th>
                  <th>Category</th>
                  <th>Tags</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {items.map((t) => (
                  <tr key={t.txn_id}>
                    <td>{shortDate(t.posted_date)}</td>
                    <td>{t.card}</td>
                    <td>
                      <MerchantCell
                        canonical={t.canonical_merchant}
                        normalized={t.normalized_merchant}
                      />
                    </td>
                    <td className="muted">{t.raw_description}</td>
                    <td className="num">{money(t.amount)}</td>
                    <td>
                      {t.category ?? <span className="unresolved">Uncategorized</span>}
                      {t.subcategory ? <span className="merchant-raw"> {t.subcategory}</span> : null}
                    </td>
                    <td>
                      <div className="tag-chip-row compact">
                        {(t.tags ?? []).map((id) => (
                          <span key={id} className="tag-chip readonly">
                            {tagLabel(id)}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <span className={`status status-${t.classified_by ?? "missing"}`}>
                        {t.classified_by ?? "open"}
                      </span>
                    </td>
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
