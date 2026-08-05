import { useCallback, useEffect, useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { money, shortDate } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import type { Transaction } from "../lib/types";

/**
 * Keyboard-first review queue. Throughput matters more than chrome here:
 * number keys pick a category, Enter accepts, s skips, u undoes the last save.
 */
export function Review() {
  const { data, loading, error, reload } = useAsync(() => api.reviewQueue(), []);
  const [index, setIndex] = useState(0);
  const [subcategory, setSubcategory] = useState("");
  const [createRule, setCreateRule] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [done, setDone] = useState<string[]>([]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const categories = data?.categories ?? [];
  const current: Transaction | undefined = items[index];

  const proposal = current?.proposed_category ?? null;

  useEffect(() => {
    setSubcategory(current?.proposed_subcategory ?? "");
    setSaveError(null);
  }, [current?.txn_id, current?.proposed_subcategory]);

  const advance = useCallback(() => {
    setIndex((i) => Math.min(i + 1, items.length));
  }, [items.length]);

  const save = useCallback(
    async (category: string) => {
      if (!current || saving) return;
      setSaving(true);
      setSaveError(null);
      try {
        await api.submitReview(current.txn_id, {
          category,
          subcategory,
          create_rule: createRule,
          rule_scope: "auto",
        });
        setDone((d) => [...d, current.txn_id]);
        advance();
      } catch (err) {
        setSaveError(err instanceof Error ? err.message : String(err));
      } finally {
        setSaving(false);
      }
    },
    [advance, createRule, current, saving, subcategory],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (!current) return;

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
  }, [advance, categories, current, index, proposal, save]);

  if (loading) return <Loading what="review queue" />;
  if (error) return <ErrorNote error={error} />;

  const remaining = items.length - index;

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
        lede={`${remaining} of ${items.length} remaining. Confirmations become reusable rules.`}
      />

      <div className="progress">
        <span style={{ width: `${(index / items.length) * 100}%` }} />
      </div>

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

          <div className="review-actions">
            <input
              type="text"
              placeholder="Subcategory (optional)"
              value={subcategory}
              onChange={(e) => setSubcategory(e.target.value)}
            />
            <label className="checkbox">
              <input
                type="checkbox"
                checked={createRule}
                onChange={(e) => setCreateRule(e.target.checked)}
              />
              Save as reusable rule
            </label>
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
    </>
  );
}
