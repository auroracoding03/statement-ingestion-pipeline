import { useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api, canWrite } from "../lib/dataSource";
import { money } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import type { UnknownCluster } from "../lib/types";

export function Merchants() {
  const merchants = useAsync(() => api.merchants(), []);
  const [threshold, setThreshold] = useState(88);
  const [withAi, setWithAi] = useState(false);
  const unknown = useAsync(() => api.unknownMerchants(threshold, withAi), [threshold, withAi]);
  const [error, setError] = useState<string | null>(null);

  function refreshAll() {
    merchants.reload();
    unknown.reload();
  }

  return (
    <>
      <PageHeader
        title="Canonical merchants"
        lede="Collapse statement variants like W*LMART, WLMRT, and WAL-MART #1234 into one brand. Confirmed names are written to config/merchants.yaml and reused on every future run."
      />

      {error && <ErrorNote error={error} />}

      {canWrite && (
        <>
          <h2>Unresolved merchant clusters</h2>
          <div className="toolbar">
            <label className="muted" htmlFor="threshold">
              Fuzzy threshold
            </label>
            <input
              id="threshold"
              type="range"
              min={60}
              max={100}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
            />
            <span className="muted">{threshold}</span>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={withAi}
                disabled={unknown.data?.ollama_available === false}
                onChange={(e) => setWithAi(e.target.checked)}
              />
              Ask local AI for brand names
              {unknown.data?.ollama_available === false && " (Ollama offline)"}
            </label>
            <button className="btn subtle small" onClick={refreshAll}>
              Refresh
            </button>
          </div>

          {unknown.loading && <Loading what="clusters" />}
          {unknown.error && <ErrorNote error={unknown.error} />}
          {!unknown.loading && (unknown.data?.items.length ?? 0) === 0 && (
            <Empty>Every merchant in the ledger has a canonical identity.</Empty>
          )}

          <div className="cluster-list">
            {(unknown.data?.items ?? []).map((cluster) => (
              <ClusterCard
                key={cluster.cluster_id}
                cluster={cluster}
                onSaved={refreshAll}
                onError={setError}
              />
            ))}
          </div>
        </>
      )}

      <h2>Curated merchants</h2>
      {merchants.loading && <Loading what="merchants" />}
      {merchants.error && <ErrorNote error={merchants.error} />}
      {(merchants.data?.items.length ?? 0) === 0 && !merchants.loading && (
        <Empty>No canonical merchants defined yet.</Empty>
      )}

      {(merchants.data?.items.length ?? 0) > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Canonical</th>
                <th>Default category</th>
                <th>Aliases</th>
                <th className="num">Transactions</th>
                <th className="num">Total</th>
                {canWrite && <th />}
              </tr>
            </thead>
            <tbody>
              {merchants.data!.items.map((m) => (
                <tr key={m.canonical}>
                  <td>
                    <strong>{m.canonical}</strong>
                  </td>
                  <td>{[m.category, m.subcategory].filter(Boolean).join(" / ") || "—"}</td>
                  <td>
                    {m.aliases.length === 0 && <span className="muted">—</span>}
                    {m.aliases.map((a, i) => (
                      <code key={i} className="tag">
                        {a.regex ?? a.exact}
                      </code>
                    ))}
                  </td>
                  <td className="num">{m.txn_count}</td>
                  <td className="num">{money(m.total_amount)}</td>
                  {canWrite && (
                    <td>
                      <button
                        className="btn danger small"
                        onClick={async () => {
                          setError(null);
                          try {
                            await api.deleteMerchant(m.canonical);
                            refreshAll();
                          } catch (err) {
                            setError(err instanceof Error ? err.message : String(err));
                          }
                        }}
                      >
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function ClusterCard({
  cluster,
  onSaved,
  onError,
}: {
  cluster: UnknownCluster;
  onSaved: () => void;
  onError: (msg: string) => void;
}) {
  const [canonical, setCanonical] = useState(
    cluster.proposed_canonical ?? titleize(cluster.representative),
  );
  const [category, setCategory] = useState("");
  const [subcategory, setSubcategory] = useState("");
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!canonical.trim()) return;
    setSaving(true);
    try {
      await api.saveMerchant({
        canonical: canonical.trim(),
        members: cluster.members,
        category: category.trim() || null,
        subcategory: subcategory.trim() || null,
        restamp: true,
      });
      onSaved();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="cluster">
      <div className="cluster-head">
        <strong>{cluster.representative}</strong>
        <span className="muted">
          {cluster.txn_count} txn · {money(cluster.total_amount)}
        </span>
      </div>

      <div style={{ marginTop: "0.5rem" }}>
        {cluster.members.map((m) => (
          <span key={m} className="tag">
            {m}
          </span>
        ))}
      </div>

      {cluster.proposed_canonical && (
        <p className="muted" style={{ marginBottom: 0, fontSize: "0.85rem" }}>
          AI proposes <strong>{cluster.proposed_canonical}</strong>
        </p>
      )}

      <div className="cluster-form">
        <input
          type="text"
          value={canonical}
          onChange={(e) => setCanonical(e.target.value)}
          placeholder="Canonical brand name"
          aria-label="Canonical brand name"
        />
        <input
          type="text"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          placeholder="Default category (optional)"
          aria-label="Default category"
        />
        <input
          type="text"
          value={subcategory}
          onChange={(e) => setSubcategory(e.target.value)}
          placeholder="Subcategory (optional)"
          aria-label="Default subcategory"
        />
        <button className="btn" onClick={save} disabled={saving || !canonical.trim()}>
          {saving ? "Saving…" : "Confirm merchant"}
        </button>
      </div>
    </div>
  );
}

function titleize(value: string): string {
  return value
    .toLowerCase()
    .split(/\s+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
