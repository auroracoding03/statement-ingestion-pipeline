import { useEffect, useMemo, useState } from "react";

import { CategoryFields } from "../components/CategoryFields";
import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api, canWrite } from "../lib/dataSource";
import { money } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import type { Merchant, MerchantOrphan, UnknownCluster } from "../lib/types";

const HIDE_KEY = "fin.merchants.hideUnresolved";

function readHidden(): boolean {
  try {
    return sessionStorage.getItem(HIDE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeHidden(hidden: boolean) {
  try {
    sessionStorage.setItem(HIDE_KEY, hidden ? "1" : "0");
  } catch {
    /* ignore quota / private mode */
  }
}

function clusterHaystack(cluster: UnknownCluster): string {
  return [
    cluster.representative,
    cluster.sample_raw,
    cluster.proposed_canonical ?? "",
    ...cluster.members,
  ]
    .join(" ")
    .toLowerCase();
}

function merchantHaystack(merchant: Merchant): string {
  const aliases = merchant.aliases.map((alias) => alias.regex ?? alias.exact ?? "").join(" ");
  return `${merchant.canonical} ${merchant.category ?? ""} ${merchant.subcategory ?? ""} ${aliases}`.toLowerCase();
}

export function Merchants() {
  const merchants = useAsync(() => api.merchants(), []);
  const rules = useAsync(() => api.rules(), []);
  const [threshold, setThreshold] = useState(88);
  const [withAi, setWithAi] = useState(false);
  const unknown = useAsync(() => api.unknownMerchants(threshold, withAi), [threshold, withAi]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hideUnresolved, setHideUnresolved] = useState(readHidden);
  const [editing, setEditing] = useState<Merchant | null>(null);
  const [merging, setMerging] = useState<{ canonical: string; txn_count: number; total_amount: number } | null>(
    null,
  );
  const categories = rules.data?.categories ?? [];
  const subcategories = rules.data?.subcategories ?? {};
  const needle = query.trim().toLowerCase();

  const clusters = useMemo(() => {
    const items = unknown.data?.items ?? [];
    if (!needle) return items;
    return items.filter((cluster) => clusterHaystack(cluster).includes(needle));
  }, [needle, unknown.data?.items]);

  const curated = useMemo(() => {
    const items = merchants.data?.items ?? [];
    if (!needle) return items;
    return items.filter((merchant) => merchantHaystack(merchant).includes(needle));
  }, [merchants.data?.items, needle]);

  const leftover = useMemo((): MerchantOrphan[] => {
    const items = merchants.data?.orphans ?? [];
    if (!needle) return items;
    return items.filter((orphan) => orphan.canonical.toLowerCase().includes(needle));
  }, [merchants.data?.orphans, needle]);

  function refreshAll() {
    merchants.reload();
    unknown.reload();
  }

  function toggleHidden() {
    const next = !hideUnresolved;
    setHideUnresolved(next);
    writeHidden(next);
  }

  return (
    <>
      <PageHeader
        title="Canonical merchants"
        lede="Collapse statement variants like W*LMART, WLMRT, and WAL-MART #1234 into one brand. Confirmed names are written to config/merchants.yaml and reused on every future run."
      />

      {error && <ErrorNote error={error} />}

      <div className="toolbar">
        <input
          className="grow"
          type="search"
          placeholder="Search unresolved clusters, leftover names, or curated merchants…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Search merchants"
        />
        {canWrite && (
          <button className="btn subtle small" type="button" onClick={toggleHidden}>
            {hideUnresolved
              ? `Show unresolved (${unknown.data?.items.length ?? 0})`
              : "Hide unresolved"}
          </button>
        )}
      </div>

      {canWrite && !hideUnresolved && (
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
          {!unknown.loading && clusters.length === 0 && (
            <Empty>
              {needle ? "No unresolved clusters match that search." : "Every merchant in the ledger has a canonical identity."}
            </Empty>
          )}

          <div className="cluster-list">
            {clusters.map((cluster) => (
              <ClusterCard
                key={cluster.cluster_id}
                cluster={cluster}
                categories={categories}
                subcategories={subcategories}
                onSaved={refreshAll}
                onError={setError}
              />
            ))}
          </div>
        </>
      )}

      {(merchants.data?.orphans?.length ?? 0) > 0 && (
        <>
          <h2>Uncatalogued ledger names</h2>
          <p className="muted">
            These names are still stamped on transactions but are no longer in merchants.yaml. Merge them into a
            curated merchant to retarget the rows.
          </p>
          {leftover.length === 0 && !merchants.loading && (
            <Empty>No leftover names match that search.</Empty>
          )}
          {leftover.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Canonical</th>
                    <th className="num">Transactions</th>
                    <th className="num">Total</th>
                    {canWrite && <th />}
                  </tr>
                </thead>
                <tbody>
                  {leftover.map((orphan) => (
                    <tr key={orphan.canonical}>
                      <td>
                        <strong>{orphan.canonical}</strong>
                      </td>
                      <td className="num">{orphan.txn_count}</td>
                      <td className="num">{money(orphan.total_amount)}</td>
                      {canWrite && (
                        <td>
                          <button
                            className="btn small"
                            type="button"
                            onClick={() => setMerging(orphan)}
                          >
                            Merge
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
      )}

      <h2>Curated merchants</h2>
      {merchants.loading && <Loading what="merchants" />}
      {merchants.error && <ErrorNote error={merchants.error} />}
      {curated.length === 0 && !merchants.loading && (
        <Empty>{needle ? "No curated merchants match that search." : "No canonical merchants defined yet."}</Empty>
      )}

      {curated.length > 0 && (
        <div className="table-wrap">
          <table className="merchants-table">
            <thead>
              <tr>
                <th>Canonical</th>
                <th>Default category</th>
                <th className="alias-cell">Aliases</th>
                <th className="num">Transactions</th>
                <th className="num">Total</th>
                {canWrite && <th className="actions-cell">Actions</th>}
              </tr>
            </thead>
            <tbody>
              {curated.map((merchant) => (
                <tr key={merchant.canonical}>
                  <td>
                    <strong>{merchant.canonical}</strong>
                  </td>
                  <td>{[merchant.category, merchant.subcategory].filter(Boolean).join(" / ") || "—"}</td>
                  <td className="alias-cell">
                    {merchant.aliases.length === 0 && <span className="muted">—</span>}
                    {merchant.aliases.map((alias, index) => (
                      <code key={index} className="tag">
                        {alias.regex ?? alias.exact}
                      </code>
                    ))}
                  </td>
                  <td className="num">{merchant.txn_count}</td>
                  <td className="num">{money(merchant.total_amount)}</td>
                  {canWrite && (
                    <td className="actions-cell">
                      <div className="toolbar" style={{ margin: 0, gap: "0.35rem" }}>
                        <button
                          className="btn small"
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setEditing(merchant);
                          }}
                        >
                          Edit
                        </button>
                        <button
                          className="btn small"
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setMerging(merchant);
                          }}
                        >
                          Merge
                        </button>
                        <button
                          className="btn danger small"
                          onClick={async () => {
                            setError(null);
                            try {
                              await api.deleteMerchant(merchant.canonical);
                              refreshAll();
                            } catch (err) {
                              setError(err instanceof Error ? err.message : String(err));
                            }
                          }}
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <MerchantEditor
          merchant={editing}
          categories={categories}
          subcategories={subcategories}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refreshAll();
          }}
          onError={setError}
        />
      )}
      {merging && (
        <MergeModal
          source={merging.canonical}
          txnCount={merging.txn_count}
          totalAmount={merging.total_amount}
          targets={(merchants.data?.items ?? []).filter(
            (merchant) => merchant.canonical.toLowerCase() !== merging.canonical.toLowerCase(),
          )}
          onClose={() => setMerging(null)}
          onSaved={() => {
            setMerging(null);
            refreshAll();
          }}
          onError={setError}
        />
      )}
    </>
  );
}

function ClusterCard({
  cluster,
  categories,
  subcategories,
  onSaved,
  onError,
}: {
  cluster: UnknownCluster;
  categories: string[];
  subcategories: Record<string, string[]>;
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
      const cleanedCategory = category.trim() || null;
      const cleanedSub = cleanedCategory && subcategory.trim() ? subcategory.trim() : null;
      await api.saveMerchant({
        canonical: canonical.trim(),
        members: cluster.members,
        category: cleanedCategory,
        subcategory: cleanedSub,
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
        {cluster.members.map((member) => (
          <span key={member} className="tag">
            {member}
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
        <CategoryFields
          categories={categories}
          subcategories={subcategories}
          category={category}
          subcategory={subcategory}
          categoryLabel="Default category (optional)"
          subcategoryLabel="Subcategory (optional)"
          onCategoryChange={(nextCategory, nextSub) => {
            setCategory(nextCategory);
            setSubcategory(nextSub);
          }}
          onPairChange={(nextCategory, nextSub) => {
            setCategory(nextCategory);
            setSubcategory(nextSub);
          }}
        />
        <button className="btn" onClick={save} disabled={saving || !canonical.trim()}>
          {saving ? "Saving…" : "Confirm merchant"}
        </button>
      </div>
    </div>
  );
}

function MerchantEditor({
  merchant,
  categories,
  subcategories,
  onClose,
  onSaved,
  onError,
}: {
  merchant: Merchant;
  categories: string[];
  subcategories: Record<string, string[]>;
  onClose: () => void;
  onSaved: () => void;
  onError: (msg: string) => void;
}) {
  const [canonical, setCanonical] = useState(merchant.canonical);
  const [category, setCategory] = useState(merchant.category ?? "");
  const [subcategory, setSubcategory] = useState(merchant.subcategory ?? "");
  const [aliases, setAliases] = useState<{ kind: "regex" | "exact"; value: string }[]>(
    merchant.aliases.length > 0
      ? merchant.aliases.map((alias) =>
          alias.regex
            ? { kind: "regex" as const, value: alias.regex }
            : { kind: "exact" as const, value: alias.exact ?? "" },
        )
      : [{ kind: "regex", value: "" }],
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    const id = window.setTimeout(() => setArmed(true), 0);
    return () => window.clearTimeout(id);
  }, []);

  async function save() {
    const cleanedAliases = aliases
      .map((alias) => ({
        regex: alias.kind === "regex" ? alias.value.trim() : undefined,
        exact: alias.kind === "exact" ? alias.value.trim() : undefined,
      }))
      .filter((alias) => alias.regex || alias.exact);
    if (!canonical.trim() || cleanedAliases.length === 0) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.updateMerchant(merchant.canonical, {
        canonical: canonical.trim(),
        aliases: cleanedAliases,
        category: category.trim() || null,
        subcategory: category.trim() && subcategory.trim() ? subcategory.trim() : null,
        apply_category: Boolean(category.trim()),
        restamp: true,
      });
      onSaved();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSaveError(message);
      onError(message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className={`modal-backdrop${armed ? "" : " is-pending"}`}
      onClick={(event) => {
        if (!armed || event.target !== event.currentTarget) return;
        onClose();
      }}
      role="presentation"
    >
      <div
        className="upload-modal merchant-edit-modal"
        role="dialog"
        aria-labelledby="merchant-edit-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="merchant-edit-title">Edit merchant</h2>
        <label>
          Canonical name
          <input value={canonical} onChange={(event) => setCanonical(event.target.value)} />
        </label>
        <div className="merchant-edit-cats">
          <CategoryFields
            categories={categories}
            subcategories={subcategories}
            category={category}
            subcategory={subcategory}
            categoryLabel="Default category (optional)"
            subcategoryLabel="Subcategory (optional)"
            onCategoryChange={(nextCategory, nextSub) => {
              setCategory(nextCategory);
              setSubcategory(nextSub);
            }}
            onPairChange={(nextCategory, nextSub) => {
              setCategory(nextCategory);
              setSubcategory(nextSub);
            }}
          />
        </div>
        <p className="muted">Aliases are regex or exact matches against the normalized merchant and raw description.</p>
        {aliases.map((alias, index) => (
          <div className="alias-row" key={index}>
            <select
              value={alias.kind}
              onChange={(event) => {
                const kind = event.target.value as "regex" | "exact";
                setAliases((current) =>
                  current.map((item, itemIndex) => (itemIndex === index ? { ...item, kind } : item)),
                );
              }}
              aria-label="Alias kind"
            >
              <option value="regex">Regex</option>
              <option value="exact">Exact</option>
            </select>
            <input
              className="grow"
              value={alias.value}
              onChange={(event) => {
                const value = event.target.value;
                setAliases((current) =>
                  current.map((item, itemIndex) => (itemIndex === index ? { ...item, value } : item)),
                );
              }}
              aria-label="Alias pattern"
            />
            <button
              className="btn ghost small"
              type="button"
              onClick={() => setAliases((current) => current.filter((_, itemIndex) => itemIndex !== index))}
              disabled={aliases.length <= 1}
            >
              Remove
            </button>
          </div>
        ))}
        <button
          className="btn subtle small"
          type="button"
          onClick={() => setAliases((current) => [...current, { kind: "regex", value: "" }])}
        >
          Add alias
        </button>
        {saveError && <ErrorNote error={saveError} />}
        <div className="review-actions">
          <button className="btn" type="button" onClick={() => void save()} disabled={saving || !canonical.trim()}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="btn subtle" type="button" onClick={onClose} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function MergeModal({
  source,
  txnCount,
  totalAmount,
  targets,
  onClose,
  onSaved,
  onError,
}: {
  source: string;
  txnCount: number;
  totalAmount: number;
  targets: Merchant[];
  onClose: () => void;
  onSaved: () => void;
  onError: (msg: string) => void;
}) {
  const [armed, setArmed] = useState(false);
  const [targetQuery, setTargetQuery] = useState("");
  const [targetName, setTargetName] = useState(targets[0]?.canonical ?? "");
  const [saving, setSaving] = useState<"apply" | "leave" | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    const id = window.setTimeout(() => setArmed(true), 0);
    return () => window.clearTimeout(id);
  }, []);

  const filteredTargets = useMemo(() => {
    const needle = targetQuery.trim().toLowerCase();
    if (!needle) return targets;
    return targets.filter((merchant) => merchantHaystack(merchant).includes(needle));
  }, [targetQuery, targets]);

  useEffect(() => {
    if (!filteredTargets.some((merchant) => merchant.canonical === targetName)) {
      setTargetName(filteredTargets[0]?.canonical ?? "");
    }
  }, [filteredTargets, targetName]);

  const selected = targets.find((merchant) => merchant.canonical === targetName);
  const defaultLabel = [selected?.category, selected?.subcategory].filter(Boolean).join(" / ");

  async function merge(applyCategory: boolean) {
    if (!targetName) return;
    setSaving(applyCategory ? "apply" : "leave");
    setSaveError(null);
    try {
      await api.mergeMerchants({ source, target: targetName, apply_category: applyCategory });
      onSaved();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSaveError(message);
      onError(message);
    } finally {
      setSaving(null);
    }
  }

  return (
    <div
      className={`modal-backdrop${armed ? "" : " is-pending"}`}
      onClick={(event) => {
        if (!armed || event.target !== event.currentTarget) return;
        onClose();
      }}
      role="presentation"
    >
      <div
        className="upload-modal merchant-edit-modal"
        role="dialog"
        aria-labelledby="merchant-merge-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="merchant-merge-title">Merge merchant</h2>
        <p>
          Fold <strong>{source}</strong> into a curated merchant.
          {txnCount > 0
            ? ` ${txnCount} transaction${txnCount === 1 ? "" : "s"} (${money(totalAmount)}) will be renamed.`
            : " No ledger rows currently use this name."}
        </p>
        <label>
          Source
          <input value={source} readOnly />
        </label>
        {targets.length > 8 && (
          <label>
            Find target
            <input
              type="search"
              value={targetQuery}
              onChange={(event) => setTargetQuery(event.target.value)}
              placeholder="Search curated merchants…"
              aria-label="Search merge targets"
            />
          </label>
        )}
        <label>
          Merge into
          <select
            value={targetName}
            onChange={(event) => setTargetName(event.target.value)}
            disabled={filteredTargets.length === 0}
            aria-label="Target merchant"
          >
            {filteredTargets.length === 0 && <option value="">No matching merchants</option>}
            {filteredTargets.map((merchant) => (
              <option key={merchant.canonical} value={merchant.canonical}>
                {merchant.canonical}
                {merchant.category ? ` — ${merchant.category}` : ""}
              </option>
            ))}
          </select>
        </label>
        {selected?.category ? (
          <p className="muted">
            Default for {selected.canonical}: {defaultLabel}
          </p>
        ) : selected ? (
          <p className="muted">{selected.canonical} has no default category.</p>
        ) : (
          <p className="muted">Confirm another curated merchant first; merge needs a target in merchants.yaml.</p>
        )}
        {saveError && <ErrorNote error={saveError} />}
        <div className="review-actions">
          <button
            className="btn"
            type="button"
            disabled={!targetName || saving !== null}
            onClick={() => void merge(true)}
          >
            {saving === "apply" ? "Merging…" : "Apply default"}
          </button>
          <button
            className="btn subtle"
            type="button"
            disabled={!targetName || saving !== null}
            onClick={() => void merge(false)}
          >
            {saving === "leave" ? "Merging…" : "Leave categories"}
          </button>
          <button className="btn ghost" type="button" onClick={onClose} disabled={saving !== null}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function titleize(value: string): string {
  return value
    .toLowerCase()
    .split(/\s+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}
