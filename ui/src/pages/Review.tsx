import { useCallback, useEffect, useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { money, shortDate } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import type { ContextTag, ReviewCluster, TagKind, Transaction } from "../lib/types";

/**
 * Keyboard-first review queue. Throughput matters more than chrome here:
 * number keys pick a category, Enter accepts, s skips, u undoes the last save.
 * Tags are secondary (mouse) so they do not steal the keyboard flow.
 */
export function Review() {
  const { data, loading, error, reload } = useAsync(() => api.reviewQueue(), []);
  const clustersState = useAsync(() => api.reviewClusters(), []);
  const tagsState = useAsync(() => api.tags(), []);
  const [mode, setMode] = useState<"one" | "cluster">("one");
  const [index, setIndex] = useState(0);
  const [subcategory, setSubcategory] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [createRule, setCreateRule] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [done, setDone] = useState<string[]>([]);
  const [newTagLabel, setNewTagLabel] = useState("");
  const [newTagKind, setNewTagKind] = useState<TagKind>("trip");

  const items = useMemo(() => data?.items ?? [], [data]);
  const queue = useMemo(() => items.filter((t) => !done.includes(t.txn_id)), [items, done]);
  const categories = data?.categories ?? [];
  const subcategories = data?.subcategories ?? {};
  const tagCatalog: ContextTag[] = tagsState.data?.items ?? [];
  const current: Transaction | undefined = queue[index];

  const proposal = current?.proposed_category ?? null;

  useEffect(() => {
    const proposed = current?.proposed_subcategory ?? "";
    const proposedCat = current?.proposed_category ?? "";
    setSubcategory(proposed && proposedCat ? `${proposedCat}::${proposed}` : "");
    setSelectedTags(current?.tags ?? []);
    setSaveError(null);
  }, [current?.txn_id, current?.proposed_category, current?.proposed_subcategory, current?.tags]);

  // Keep cursor valid when cascade-reclassify shrinks the queue under us.
  useEffect(() => {
    if (index > 0 && index >= queue.length) {
      setIndex(Math.max(0, queue.length - 1));
    }
  }, [index, queue.length]);

  const advance = useCallback(() => {
    setIndex((i) => Math.min(i + 1, queue.length));
  }, [queue.length]);

  const resolveSubcategory = useCallback(
    (category: string) => {
      // Select values are "Primary::Subcategory" so names can repeat across primaries.
      if (subcategory.includes("::")) {
        const [pickedCategory, ...rest] = subcategory.split("::");
        const pickedSub = rest.join("::");
        if (pickedCategory === category && pickedSub) return pickedSub;
        return "";
      }
      const allowed = subcategories[category] ?? [];
      if (subcategory && allowed.includes(subcategory)) return subcategory;
      if (
        proposal === category &&
        current?.proposed_subcategory &&
        subcategory === current.proposed_subcategory
      ) {
        return subcategory;
      }
      return "";
    },
    [current?.proposed_subcategory, proposal, subcategory, subcategories],
  );

  const save = useCallback(
    async (category: string) => {
      if (!current || saving) return;
      setSaving(true);
      setSaveError(null);
      try {
        const result = await api.submitReview(current.txn_id, {
          category,
          subcategory: resolveSubcategory(category),
          tags: selectedTags,
          create_rule: createRule,
          rule_scope: "auto",
        });
        const applied = result.applied_txn_ids ?? [];
        // Drop this txn and any siblings the new rule auto-classified. Index stays
        // put so the next remaining item slides into place.
        setDone((d) => [...d, current.txn_id, ...applied]);
        clustersState.reload();
      } catch (err) {
        setSaveError(err instanceof Error ? err.message : String(err));
      } finally {
        setSaving(false);
      }
    },
    [createRule, current, resolveSubcategory, saving, selectedTags, clustersState.reload],
  );

  function toggleTag(tagId: string) {
    setSelectedTags((currentTags) =>
      currentTags.includes(tagId) ? currentTags.filter((id) => id !== tagId) : [...currentTags, tagId],
    );
  }

  async function createTag() {
    if (!newTagLabel.trim()) return;
    setSaveError(null);
    try {
      const result = await api.createTag({ label: newTagLabel.trim(), kind: newTagKind });
      setSelectedTags((currentTags) =>
        currentTags.includes(result.tag.id) ? currentTags : [...currentTags, result.tag.id],
      );
      setNewTagLabel("");
      tagsState.reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (mode !== "one" || !current) return;

      if (e.key === "Enter" && proposal) {
        e.preventDefault();
        void save(proposal);
        return;
      }
      if (e.key === "s") {
        e.preventDefault();
        advance();
        return;
      }
      if (e.key === "u" && index > 0) {
        e.preventDefault();
        setIndex((i) => Math.max(0, i - 1));
        return;
      }
      const num = Number(e.key);
      if (!Number.isNaN(num) && num >= 1 && num <= Math.min(9, categories.length)) {
        e.preventDefault();
        void save(categories[num - 1]);
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [advance, categories, current, index, mode, proposal, save]);

  const previewCategory = proposal || categories[0] || "Uncategorized";
  const preview = useAsync(
    () =>
      current && createRule
        ? api.previewRule({
            txn_id: current.txn_id,
            category: previewCategory,
            subcategory: resolveSubcategory(previewCategory),
            rule_scope: "auto",
          })
        : Promise.resolve({ match_count: 0, sample: [] }),
    [current?.txn_id, createRule, previewCategory, subcategory],
  );

  async function approveCluster(cluster: ReviewCluster, category: string) {
    setSaving(true);
    setSaveError(null);
    try {
      const result = await api.submitReview(cluster.representative_txn_id, {
        category,
        subcategory: cluster.proposed_subcategory || "",
        create_rule: true,
        rule_scope: "auto",
      });
      const applied = result.applied_txn_ids ?? [];
      setDone((d) => [...d, cluster.representative_txn_id, ...applied]);
      clustersState.reload();
      reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <Loading what="review queue" />;
  if (error) return <ErrorNote error={error} />;

  const remaining = queue.length;
  const progressDone = items.length === 0 ? 0 : ((items.length - remaining) / items.length) * 100;

  if (!current) {
    return (
      <>
        <PageHeader title="Review queue" />
        <Empty>
          {done.length > 0
            ? `Reviewed ${done.length} transaction(s). Queue is clear.`
            : "Nothing needs review. Every transaction has a confirmed category."}
        </Empty>
        <button className="btn" onClick={() => { setIndex(0); setDone([]); reload(); }}>
          Refresh queue
        </button>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Review queue"
        lede={`${remaining} of ${items.length} remaining. Confirmations become reusable rules. Tags are optional context.`}
      />

      <div className="toolbar">
        <button
          type="button"
          className={`btn subtle small${mode === "one" ? " selected" : ""}`}
          onClick={() => setMode("one")}
        >
          One by one
        </button>
        <button
          type="button"
          className={`btn subtle small${mode === "cluster" ? " selected" : ""}`}
          onClick={() => setMode("cluster")}
        >
          By merchant
        </button>
      </div>

      <div className="progress">
        <span style={{ width: `${progressDone}%` }} />
      </div>

      {mode === "cluster" ? (
        <ClusterMode
          clusters={clustersState.data?.items ?? []}
          loading={clustersState.loading}
          error={clustersState.error}
          categories={categories}
          saving={saving}
          saveError={saveError}
          onApprove={approveCluster}
        />
      ) : (
      <div className="review-layout">
        <div className="review-card">
          <div className="review-amount">{money(current.amount)}</div>

          <dl className="review-fields">
            <dt>Date</dt>
            <dd>{shortDate(current.posted_date)}</dd>
            <dt>Card</dt>
            <dd>{current.card}</dd>
            <dt>Raw</dt>
            <dd>{current.raw_description}</dd>
            <dt>Normalized</dt>
            <dd>{current.normalized_merchant}</dd>
            <dt>Canonical</dt>
            <dd>
              {current.canonical_merchant ?? (
                <span className="unresolved">none — add one on the Merchants page</span>
              )}
            </dd>
          </dl>

          {proposal && (
            <div className="proposal">
              AI proposes <strong>{proposal}</strong>
              {current.proposed_subcategory ? ` / ${current.proposed_subcategory}` : ""} — press{" "}
              <code>Enter</code> to accept.
            </div>
          )}

          <div className="category-grid">
            {categories.map((category, i) => (
              <button
                key={category}
                className={proposal === category ? "selected" : ""}
                disabled={saving}
                onClick={() => void save(category)}
              >
                {i < 9 && <span className="key">{i + 1}</span>}
                {category}
              </button>
            ))}
          </div>

          <div className="tag-picker">
            <p className="muted">Context tags (optional)</p>
            <div className="tag-chip-row">
              {tagCatalog.map((tag) => {
                const active = selectedTags.includes(tag.id);
                return (
                  <button
                    key={tag.id}
                    type="button"
                    className={`tag-chip ${active ? "active" : ""}`}
                    onClick={() => toggleTag(tag.id)}
                    disabled={saving}
                  >
                    {tag.label}
                    <span className="tag-kind">{tag.kind}</span>
                  </button>
                );
              })}
            </div>
            <div className="toolbar" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
              <input
                type="text"
                placeholder="New tag (e.g. London-Paris)"
                value={newTagLabel}
                onChange={(e) => setNewTagLabel(e.target.value)}
              />
              <select value={newTagKind} onChange={(e) => setNewTagKind(e.target.value as TagKind)}>
                <option value="trip">Trip</option>
                <option value="occasion">Occasion</option>
                <option value="other">Other</option>
              </select>
              <button className="btn subtle" type="button" onClick={() => void createTag()} disabled={saving}>
                Create tag
              </button>
            </div>
          </div>

          <div className="review-actions">
            <select
              value={subcategory}
              onChange={(e) => setSubcategory(e.target.value)}
              aria-label="Subcategory"
            >
              <option value="">Subcategory (optional)</option>
              {categories.map((category) => {
                const options = [...(subcategories[category] ?? [])];
                if (
                  proposal === category &&
                  current.proposed_subcategory &&
                  !options.includes(current.proposed_subcategory)
                ) {
                  options.push(current.proposed_subcategory);
                }
                if (options.length === 0) return null;
                return (
                  <optgroup key={category} label={category}>
                    {options.map((sub) => (
                      <option key={`${category}::${sub}`} value={`${category}::${sub}`}>
                        {sub}
                      </option>
                    ))}
                  </optgroup>
                );
              })}
            </select>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={createRule}
                onChange={(e) => setCreateRule(e.target.checked)}
              />
              Save as reusable rule
            </label>
            {createRule && (preview.data?.match_count ?? 0) > 0 && (
              <span className="muted">
                This rule would classify {preview.data?.match_count} open transaction
                {(preview.data?.match_count ?? 0) === 1 ? "" : "s"}
              </span>
            )}
            <button className="btn subtle" onClick={advance} disabled={saving}>
              Skip
            </button>
          </div>

          {saveError && <ErrorNote error={saveError} />}
        </div>

        <aside className="shortcuts">
          <h3>Shortcuts</h3>
          <dl>
            <dt>1-9</dt>
            <dd>Pick category</dd>
            <dt>Enter</dt>
            <dd>Accept AI proposal</dd>
            <dt>s</dt>
            <dd>Skip</dd>
            <dt>u</dt>
            <dd>Back one</dd>
          </dl>
          <p className="muted" style={{ marginBottom: 0, marginTop: "0.9rem" }}>
            Reviewed this session: {done.length}
          </p>
        </aside>
      </div>
      )}
    </>
  );
}

function ClusterMode({
  clusters,
  loading,
  error,
  categories,
  saving,
  saveError,
  onApprove,
}: {
  clusters: ReviewCluster[];
  loading: boolean;
  error: string | null;
  categories: string[];
  saving: boolean;
  saveError: string | null;
  onApprove: (cluster: ReviewCluster, category: string) => Promise<void>;
}) {
  if (loading) return <Loading what="merchant groups" />;
  if (error) return <ErrorNote error={error} />;
  if (clusters.length === 0) return <Empty>No open merchant groups.</Empty>;
  return (
    <>
      {saveError && <ErrorNote error={saveError} />}
      <div className="cluster-list">
        {clusters.map((cluster) => (
          <ClusterReviewCard
            key={cluster.key}
            cluster={cluster}
            categories={categories}
            saving={saving}
            onApprove={onApprove}
          />
        ))}
      </div>
    </>
  );
}

function ClusterReviewCard({
  cluster,
  categories,
  saving,
  onApprove,
}: {
  cluster: ReviewCluster;
  categories: string[];
  saving: boolean;
  onApprove: (cluster: ReviewCluster, category: string) => Promise<void>;
}) {
  const proposed = cluster.proposed_category;
  const [category, setCategory] = useState(proposed ?? "");
  return (
    <article className="cluster">
      <div className="cluster-head">
        <div>
          <strong>{cluster.merchant}</strong>
          <p className="muted" style={{ margin: "0.2rem 0 0" }}>
            {cluster.count} transaction{cluster.count === 1 ? "" : "s"} · {money(cluster.total_amount)}
            {proposed ? ` → ${proposed}${cluster.proposed_subcategory ? ` / ${cluster.proposed_subcategory}` : ""}` : ""}
          </p>
        </div>
        {category ? (
          <button className="btn" disabled={saving} onClick={() => void onApprove(cluster, category)}>
            Approve {cluster.count} and save rule
          </button>
        ) : null}
      </div>
      <div className="category-grid" style={{ marginTop: "0.8rem", marginBottom: 0 }}>
        {categories.map((name) => (
          <button
            key={name}
            type="button"
            className={category === name ? "selected" : ""}
            disabled={saving}
            onClick={() => setCategory(name)}
          >
            {name}
          </button>
        ))}
      </div>
    </article>
  );
}
