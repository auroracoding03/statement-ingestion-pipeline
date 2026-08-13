import { useEffect, useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { useAsync } from "../lib/useAsync";
import type { Rule, TagKind } from "../lib/types";

type EditDraft = { category: string; subcategory: string };

type DeleteTarget = { category: string; subcategory?: string };

type CategoryImpact = {
  category: string;
  subcategory: string | null;
  txn_count: number;
  rule_count: number;
  merchant_count: number;
  bill_count: number;
};

function vocabLabel(category: string, subcategory?: string | null) {
  return subcategory ? `${category} / ${subcategory}` : category;
}

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
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft>({ category: "", subcategory: "" });
  const [savingEdit, setSavingEdit] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [impact, setImpact] = useState<CategoryImpact | null>(null);
  const [reassignCategory, setReassignCategory] = useState("");
  const [reassignSubcategory, setReassignSubcategory] = useState("");
  const [deleting, setDeleting] = useState(false);

  const categories = data?.categories ?? [];
  const subcategories = data?.subcategories ?? {};
  const tags = tagsState.data?.items ?? [];
  const formSubs = useMemo(
    () => (form.category ? subcategories[form.category] ?? [] : []),
    [form.category, subcategories],
  );
  const editSubs = useMemo(
    () => (editDraft.category ? subcategories[editDraft.category] ?? [] : []),
    [editDraft.category, subcategories],
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

  function startEdit(rule: Rule) {
    setSaveError(null);
    setEditingIndex(rule.index);
    setEditDraft({
      category: rule.category ?? "",
      subcategory: rule.subcategory ?? "",
    });
  }

  function cancelEdit() {
    setEditingIndex(null);
    setEditDraft({ category: "", subcategory: "" });
  }

  async function openDelete(category: string, subcategory?: string) {
    setSaveError(null);
    setReassignCategory("");
    setReassignSubcategory("");
    setImpact(null);
    setDeleteTarget({ category, subcategory });
    try {
      setImpact(await api.categoryImpact(category, subcategory));
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  }

  function closeDelete() {
    setDeleteTarget(null);
    setImpact(null);
    setReassignCategory("");
    setReassignSubcategory("");
  }

  async function confirmDelete(action: "unassign" | "reassign") {
    if (!deleteTarget) return;
    if (action === "reassign" && !reassignCategory.trim()) return;
    setDeleting(true);
    setSaveError(null);
    try {
      await api.deleteCategory({
        category: deleteTarget.category,
        subcategory: deleteTarget.subcategory,
        action,
        reassign_category: action === "reassign" ? reassignCategory : undefined,
        reassign_subcategory: action === "reassign" ? reassignSubcategory : undefined,
      });
      closeDelete();
      reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  }

  async function saveEdit(index: number) {
    if (!editDraft.category.trim()) return;
    setSaveError(null);
    setSavingEdit(true);
    try {
      await api.updateRule(index, {
        category: editDraft.category.trim(),
        subcategory: editDraft.subcategory.trim(),
      });
      cancelEdit();
      reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingEdit(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Classification rules"
        lede="Manage primary categories, subcategories, and context tags, then ordered rules. Category totals use the primary only; tags are for filtering. After editing a rule, re-run Classify to apply it to the ledger."
      />
      {saveError && !deleteTarget && <ErrorNote error={saveError} />}

      <section className="panel taxonomy-panel">
        <h2>Primary categories</h2>
        <p className="muted">These drive monthly rollups and review category buttons.</p>
        <div className="tag-chip-row">
          {categories.map((category) => (
            <span key={category} className="tag-chip readonly">
              {category}
              {category !== "Uncategorized" && (
                <button
                  type="button"
                  className="tag-remove"
                  title={`Remove ${category}`}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    void openDelete(category);
                  }}
                >
                  ×
                </button>
              )}
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
                      <button
                        type="button"
                        className="tag-remove"
                        title={`Remove ${category} / ${sub}`}
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                          void openDelete(category, sub);
                        }}
                      >
                        ×
                      </button>
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
        {saveError && !deleteTarget && <ErrorNote error={saveError} />}
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
                const isEditing = editingIndex === rule.index;
                return (
                  <tr key={rule.index}>
                    <td className="num muted">{rule.index}</td>
                    <td>{kind}</td>
                    <td>
                      <code>{pattern}</code>
                    </td>
                    <td>
                      {isEditing ? (
                        <div className="toolbar" style={{ margin: 0, gap: "0.35rem" }}>
                          <select
                            value={editDraft.category}
                            onChange={(e) =>
                              setEditDraft({ category: e.target.value, subcategory: "" })
                            }
                            disabled={savingEdit}
                          >
                            <option value="">Category</option>
                            {categories.map((category) => (
                              <option key={category} value={category}>
                                {category}
                              </option>
                            ))}
                          </select>
                          <select
                            value={editDraft.subcategory}
                            onChange={(e) =>
                              setEditDraft({ ...editDraft, subcategory: e.target.value })
                            }
                            disabled={!editDraft.category || savingEdit}
                          >
                            <option value="">Subcategory (optional)</option>
                            {editSubs.map((sub) => (
                              <option key={sub} value={sub}>
                                {sub}
                              </option>
                            ))}
                          </select>
                        </div>
                      ) : (
                        [rule.category, rule.subcategory].filter(Boolean).join(" / ")
                      )}
                    </td>
                    <td>
                      {isEditing ? (
                        <div className="toolbar" style={{ margin: 0, gap: "0.35rem" }}>
                          <button
                            className="btn small"
                            type="button"
                            disabled={savingEdit || !editDraft.category.trim()}
                            onClick={() => void saveEdit(rule.index)}
                          >
                            Save
                          </button>
                          <button
                            className="btn small"
                            type="button"
                            disabled={savingEdit}
                            onClick={cancelEdit}
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="toolbar" style={{ margin: 0, gap: "0.35rem" }}>
                          <button
                            className="btn small"
                            type="button"
                            onClick={() => startEdit(rule)}
                          >
                            Edit
                          </button>
                          <button
                            className="btn danger small"
                            type="button"
                            onClick={async () => {
                              setSaveError(null);
                              try {
                                await api.deleteRule(rule.index);
                                if (editingIndex === rule.index) cancelEdit();
                                reload();
                              } catch (err) {
                                setSaveError(err instanceof Error ? err.message : String(err));
                              }
                            }}
                          >
                            Remove
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {deleteTarget && (
        <CategoryDeleteModal
          target={deleteTarget}
          impact={impact}
          categories={categories}
          subcategories={subcategories}
          reassignCategory={reassignCategory}
          reassignSubcategory={reassignSubcategory}
          deleting={deleting}
          error={saveError}
          onReassignCategory={(next) => {
            setReassignCategory(next);
            setReassignSubcategory("");
          }}
          onReassignSubcategory={setReassignSubcategory}
          onUnassign={() => void confirmDelete("unassign")}
          onReassign={() => void confirmDelete("reassign")}
          onClose={closeDelete}
        />
      )}
    </>
  );
}

function CategoryDeleteModal({
  target,
  impact,
  categories,
  subcategories,
  reassignCategory,
  reassignSubcategory,
  deleting,
  error,
  onReassignCategory,
  onReassignSubcategory,
  onUnassign,
  onReassign,
  onClose,
}: {
  target: DeleteTarget;
  impact: CategoryImpact | null;
  categories: string[];
  subcategories: Record<string, string[]>;
  reassignCategory: string;
  reassignSubcategory: string;
  deleting: boolean;
  error: string | null;
  onReassignCategory: (category: string) => void;
  onReassignSubcategory: (subcategory: string) => void;
  onUnassign: () => void;
  onReassign: () => void;
  onClose: () => void;
}) {
  const label = vocabLabel(target.category, target.subcategory);
  const reassignCategories = categories.filter((category) => {
    if (category === "Uncategorized") return false;
    if (!target.subcategory && category === target.category) return false;
    return true;
  });
  const reassignSubs = (reassignCategory ? subcategories[reassignCategory] ?? [] : []).filter((sub) => {
    if (target.subcategory && reassignCategory === target.category && sub === target.subcategory) {
      return false;
    }
    return true;
  });
  const samePair =
    Boolean(target.subcategory) &&
    reassignCategory === target.category &&
    reassignSubcategory === target.subcategory;
  const canReassign = Boolean(reassignCategory.trim()) && !samePair;
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    const id = window.setTimeout(() => setArmed(true), 0);
    return () => window.clearTimeout(id);
  }, []);

  return (
    <div
      className={`modal-backdrop${armed ? "" : " is-pending"}`}
      onClick={(event) => {
        if (!armed || deleting || event.target !== event.currentTarget) return;
        onClose();
      }}
      role="presentation"
    >
      <div
        className="upload-modal category-delete-modal"
        role="dialog"
        aria-labelledby="category-delete-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="category-delete-title">Delete {label}</h2>
        <div className="category-delete-copy">
          {impact ? (
            <>
              <p>
                <strong>{label}</strong> is assigned to <strong>{impact.txn_count} transactions</strong>.
                Deleting it will unwrite those assignments.
              </p>
              <p className="muted">
                Also used by: {impact.rule_count} rules, {impact.merchant_count} merchant defaults.
              </p>
            </>
          ) : (
            <p className="muted">Checking where this category is used…</p>
          )}
        </div>
        <div className="category-delete-reassign">
          <select
            value={reassignCategory}
            onChange={(event) => onReassignCategory(event.target.value)}
            disabled={deleting}
            aria-label="Reassign category"
          >
            <option value="">Reassign to category</option>
            {reassignCategories.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
          <select
            value={reassignSubcategory}
            onChange={(event) => onReassignSubcategory(event.target.value)}
            disabled={deleting || !reassignCategory}
            aria-label="Reassign subcategory"
          >
            <option value="">Subcategory (optional)</option>
            {reassignSubs.map((sub) => (
              <option key={sub} value={sub}>
                {sub}
              </option>
            ))}
          </select>
        </div>
        {error && <ErrorNote error={error} />}
        <div className="review-actions">
          <button className="btn danger" type="button" onClick={onUnassign} disabled={deleting || !impact}>
            {deleting ? "Working…" : "Delete and unassign"}
          </button>
          <button
            className="btn"
            type="button"
            onClick={onReassign}
            disabled={deleting || !impact || !canReassign}
          >
            Reassign and delete
          </button>
          <button className="btn subtle" type="button" onClick={onClose} disabled={deleting}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
