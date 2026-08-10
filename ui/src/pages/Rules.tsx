import { useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { useAsync } from "../lib/useAsync";
import type { TagKind } from "../lib/types";

export function Rules() {
  const { data, loading, error, reload } = useAsync(() => api.rules(), []);
  const tagsState = useAsync(() => api.tags(), []);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [form, setForm] = useState({
    kind: "canonical" as "canonical" | "regex",
    value: "",
    category: "",
    subcategory: "",
  });
  const [newCategory, setNewCategory] = useState("");
  const [newSubcategory, setNewSubcategory] = useState({ category: "", subcategory: "" });
  const [newTag, setNewTag] = useState({ label: "", kind: "trip" as TagKind });

  const categories = data?.categories ?? [];
  const subcategories = data?.subcategories ?? {};
  const tags = tagsState.data?.items ?? [];
  const formSubs = useMemo(
    () => (form.category ? subcategories[form.category] ?? [] : []),
    [form.category, subcategories],
  );
  const tagsByKind = useMemo(() => {
    const groups: Record<string, typeof tags> = { occasion: [], trip: [], other: [] };
    for (const tag of tags) {
      (groups[tag.kind] ?? groups.other).push(tag);
    }
    return groups;
  }, [tags]);

  async function addRule() {
    if (!form.value.trim() || !form.category.trim()) return;
    setSaveError(null);
    try {
      await api.saveRule({
        [form.kind === "canonical" ? "merchant_canonical" : "merchant_regex"]: form.value.trim(),
        category: form.category.trim(),
        subcategory: form.subcategory.trim(),
      });
      setForm({ ...form, value: "", subcategory: "" });
      reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  }

  async function addPrimary() {
    if (!newCategory.trim()) return;
    setSaveError(null);
    try {
      await api.addCategory(newCategory.trim());
      setNewCategory("");
      reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  }

  async function addSubcategory() {
    if (!newSubcategory.category.trim() || !newSubcategory.subcategory.trim()) return;
    setSaveError(null);
    try {
      await api.addSubcategory(newSubcategory.category.trim(), newSubcategory.subcategory.trim());
      setNewSubcategory({ category: newSubcategory.category, subcategory: "" });
      reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  }

  async function addTag() {
    if (!newTag.label.trim()) return;
    setSaveError(null);
    try {
      await api.createTag({ label: newTag.label.trim(), kind: newTag.kind });
      setNewTag({ label: "", kind: "trip" });
      tagsState.reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <PageHeader
        title="Classification rules"
        lede="Manage primary categories, subcategories, and context tags, then ordered rules. Category totals use the primary only; tags are for filtering."
      />

      <section className="panel taxonomy-panel">
        <h2>Primary categories</h2>
        <p className="muted">These drive monthly rollups and review category buttons.</p>
        <div className="tag-chip-row">
          {categories.map((category) => (
            <span key={category} className="tag-chip readonly">
              {category}
            </span>
          ))}
        </div>
        <div className="toolbar" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
          <input
            type="text"
            placeholder="Add primary (e.g. Travel)"
            value={newCategory}
            onChange={(e) => setNewCategory(e.target.value)}
          />
          <button className="btn" type="button" onClick={() => void addPrimary()}>
            Add primary
          </button>
        </div>
      </section>

      <section className="panel taxonomy-panel">
        <h2>Subcategories</h2>
        <p className="muted">Managed per primary so values stay consistent (Food → FastFood, Transport → Gas).</p>
        <div className="subcategory-vocab">
          {categories.map((category) => {
            const items = subcategories[category] ?? [];
            return (
              <div key={category} className="subcategory-vocab-row">
                <strong>{category}</strong>
                <div className="tag-chip-row">
                  {items.length === 0 && <span className="muted">None yet</span>}
                  {items.map((sub) => (
                    <span key={sub} className="tag-chip readonly">
                      {sub}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
        <div className="toolbar" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
          <select
            value={newSubcategory.category}
            onChange={(e) => setNewSubcategory({ ...newSubcategory, category: e.target.value })}
          >
            <option value="">Primary</option>
            {categories.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder="New subcategory (e.g. FastFood)"
            value={newSubcategory.subcategory}
            onChange={(e) => setNewSubcategory({ ...newSubcategory, subcategory: e.target.value })}
          />
          <button className="btn" type="button" onClick={() => void addSubcategory()}>
            Add subcategory
          </button>
        </div>
      </section>

      <section className="panel taxonomy-panel">
        <h2>Context tags</h2>
        <p className="muted">Occasions and trips (London-Paris) attach to transactions without changing category totals.</p>
        {(["occasion", "trip", "other"] as const).map((kind) => (
          <div key={kind} style={{ marginBottom: "0.75rem" }}>
            <h3 className="muted" style={{ textTransform: "capitalize", marginBottom: "0.35rem" }}>
              {kind}
            </h3>
            <div className="tag-chip-row">
              {tagsByKind[kind].length === 0 && <span className="muted">None yet</span>}
              {tagsByKind[kind].map((tag) => (
                <span key={tag.id} className="tag-chip readonly">
                  {tag.label}
                  <button
                    type="button"
                    className="tag-remove"
                    title={`Remove ${tag.label}`}
                    onClick={async () => {
                      setSaveError(null);
                      try {
                        await api.deleteTag(tag.id);
                        tagsState.reload();
                      } catch (err) {
                        setSaveError(err instanceof Error ? err.message : String(err));
                      }
                    }}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>
        ))}
        <div className="toolbar" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
          <input
            type="text"
            placeholder="New tag label"
            value={newTag.label}
            onChange={(e) => setNewTag({ ...newTag, label: e.target.value })}
          />
          <select
            value={newTag.kind}
            onChange={(e) => setNewTag({ ...newTag, kind: e.target.value as TagKind })}
          >
            <option value="trip">Trip</option>
            <option value="occasion">Occasion</option>
            <option value="other">Other</option>
          </select>
          <button className="btn" type="button" onClick={() => void addTag()}>
            Add tag
          </button>
        </div>
      </section>

      <div className="panel" style={{ marginBottom: "1.5rem" }}>
        <h2>Rules</h2>
        <div className="toolbar" style={{ marginBottom: 0 }}>
          <select
            value={form.kind}
            onChange={(e) => setForm({ ...form, kind: e.target.value as "canonical" | "regex" })}
          >
            <option value="canonical">Canonical merchant</option>
            <option value="regex">Merchant regex</option>
          </select>
          <input
            className="grow"
            type="text"
            placeholder={form.kind === "canonical" ? "Walmart" : "(?i)wal[-\\s]?mart"}
            value={form.value}
            onChange={(e) => setForm({ ...form, value: e.target.value })}
          />
          <select
            value={form.category}
            onChange={(e) => setForm({ ...form, category: e.target.value, subcategory: "" })}
          >
            <option value="">Category</option>
            {categories.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
          <select
            value={form.subcategory}
            onChange={(e) => setForm({ ...form, subcategory: e.target.value })}
            disabled={!form.category}
          >
            <option value="">Subcategory (optional)</option>
            {formSubs.map((sub) => (
              <option key={sub} value={sub}>
                {sub}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => void addRule()}>
            Add rule
          </button>
        </div>
        {saveError && <ErrorNote error={saveError} />}
      </div>

      {loading && <Loading what="rules" />}
      {error && <ErrorNote error={error} />}
      {!loading && (data?.rules.length ?? 0) === 0 && <Empty>No rules defined.</Empty>}

      {(data?.rules.length ?? 0) > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Match</th>
                <th>Pattern</th>
                <th>Category</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data!.rules.map((rule) => {
                const kind = rule.match.merchant_canonical
                  ? "canonical"
                  : rule.match.merchant_exact
                    ? "exact"
                    : "regex";
                const pattern =
                  rule.match.merchant_canonical ??
                  rule.match.merchant_exact ??
                  rule.match.merchant_regex ??
                  "";
                return (
                  <tr key={rule.index}>
                    <td className="num muted">{rule.index}</td>
                    <td>{kind}</td>
                    <td>
                      <code>{pattern}</code>
                    </td>
                    <td>{[rule.category, rule.subcategory].filter(Boolean).join(" / ")}</td>
                    <td>
                      <button
                        className="btn danger small"
                        onClick={async () => {
                          setSaveError(null);
                          try {
                            await api.deleteRule(rule.index);
                            reload();
                          } catch (err) {
                            setSaveError(err instanceof Error ? err.message : String(err));
                          }
                        }}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
