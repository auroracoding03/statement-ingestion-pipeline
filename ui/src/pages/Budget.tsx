import { useEffect, useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import type { BudgetEnvelope } from "../lib/types";
import { useAsync } from "../lib/useAsync";

function parseAmount(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const amount = Number(trimmed);
  return Number.isFinite(amount) ? amount : null;
}

function amountInput(value: number | null): string {
  return value == null ? "" : String(value);
}

export function Budget() {
  const remote = useAsync(() => api.budget(), []);
  const [envelopes, setEnvelopes] = useState<BudgetEnvelope[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState<Record<string, { pick: string; name: string; amount: string }>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (remote.data?.envelopes) {
      setEnvelopes(remote.data.envelopes);
      setDrafts({});
    }
  }, [remote.data]);

  async function persist(next: BudgetEnvelope[]) {
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await api.saveBudget(next);
      setEnvelopes(saved.envelopes);
      setDrafts({});
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  function updateCategory(category: string, patch: Partial<BudgetEnvelope>) {
    const next = envelopes.map((env) => (env.category === category ? { ...env, ...patch } : env));
    setEnvelopes(next);
    return next;
  }

  function draftKey(category: string, subcategory?: string) {
    return subcategory ? `${category}::${subcategory}` : category;
  }

  function shownAmount(category: string, stored: number | null, subcategory?: string) {
    const key = draftKey(category, subcategory);
    return key in drafts ? drafts[key] : amountInput(stored);
  }

  return (
    <>
      <PageHeader
        title="Budget"
        lede="Monthly envelopes over your existing categories. Check Show on Overview to put a row on the household spend table."
      />

      {remote.error && <ErrorNote error={remote.error} />}
      {saveError && <ErrorNote error={saveError} />}
      {remote.loading && envelopes.length === 0 && <Loading what="budget" />}
      {!remote.loading && envelopes.length === 0 && <Empty>Add categories on the Rules page first.</Empty>}

      {envelopes.length > 0 && (
        <div className="table-wrap">
          <table className="budget-table">
            <thead>
              <tr>
                <th>Show on Overview</th>
                <th>Category</th>
                <th className="num">Budgeted Spend</th>
              </tr>
            </thead>
            <tbody>
              {envelopes.map((env) => {
                const add = adding[env.category] ?? { pick: "", name: "", amount: "" };
                return (
                  <CategoryBlock
                    key={env.category}
                    env={env}
                    add={add}
                    shownAmount={shownAmount}
                    saving={saving}
                    onToggle={(checked) => {
                      void persist(updateCategory(env.category, { show_on_overview: checked }));
                    }}
                    onAmountDraft={(value) => {
                      setDrafts((current) => ({ ...current, [draftKey(env.category)]: value }));
                    }}
                    onAmountCommit={(value) => {
                      void persist(updateCategory(env.category, { amount: parseAmount(value) }));
                    }}
                    onToggleSub={(subcategory, checked) => {
                      const next = envelopes.map((item) =>
                        item.category !== env.category
                          ? item
                          : {
                              ...item,
                              subcategories: item.subcategories.map((sub) =>
                                sub.subcategory === subcategory ? { ...sub, show_on_overview: checked } : sub,
                              ),
                            },
                      );
                      setEnvelopes(next);
                      void persist(next);
                    }}
                    onSubDraft={(subcategory, value) => {
                      setDrafts((current) => ({ ...current, [draftKey(env.category, subcategory)]: value }));
                    }}
                    onSubCommit={(subcategory, value) => {
                      const next = envelopes.map((item) =>
                        item.category !== env.category
                          ? item
                          : {
                              ...item,
                              subcategories: item.subcategories.map((sub) =>
                                sub.subcategory === subcategory ? { ...sub, amount: parseAmount(value) } : sub,
                              ),
                            },
                      );
                      setEnvelopes(next);
                      void persist(next);
                    }}
                    onRemoveSub={(subcategory) => {
                      const next = envelopes.map((item) =>
                        item.category !== env.category
                          ? item
                          : {
                              ...item,
                              subcategories: item.subcategories.filter((sub) => sub.subcategory !== subcategory),
                            },
                      );
                      setEnvelopes(next);
                      void persist(next);
                    }}
                    onAddChange={(patch) => {
                      setAdding((current) => ({ ...current, [env.category]: { ...add, ...patch } }));
                    }}
                    onAdd={() => {
                      const name = add.name.trim() || add.pick.trim();
                      if (!name) return;
                      if (env.subcategories.some((sub) => sub.subcategory === name)) {
                        setAdding((current) => ({ ...current, [env.category]: { pick: "", name: "", amount: "" } }));
                        return;
                      }
                      const next = envelopes.map((item) =>
                        item.category !== env.category
                          ? item
                          : {
                              ...item,
                              subcategories: [
                                ...item.subcategories,
                                {
                                  subcategory: name,
                                  amount: parseAmount(add.amount),
                                  show_on_overview: false,
                                },
                              ],
                            },
                      );
                      setEnvelopes(next);
                      setAdding((current) => ({ ...current, [env.category]: { pick: "", name: "", amount: "" } }));
                      void persist(next);
                    }}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function CategoryBlock({
  env,
  add,
  shownAmount,
  saving,
  onToggle,
  onAmountDraft,
  onAmountCommit,
  onToggleSub,
  onSubDraft,
  onSubCommit,
  onRemoveSub,
  onAddChange,
  onAdd,
}: {
  env: BudgetEnvelope;
  add: { pick: string; name: string; amount: string };
  shownAmount: (category: string, stored: number | null, subcategory?: string) => string;
  saving: boolean;
  onToggle: (checked: boolean) => void;
  onAmountDraft: (value: string) => void;
  onAmountCommit: (value: string) => void;
  onToggleSub: (subcategory: string, checked: boolean) => void;
  onSubDraft: (subcategory: string, value: string) => void;
  onSubCommit: (subcategory: string, value: string) => void;
  onRemoveSub: (subcategory: string) => void;
  onAddChange: (patch: Partial<{ pick: string; name: string; amount: string }>) => void;
  onAdd: () => void;
}) {
  return (
    <>
      <tr>
        <td>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={env.show_on_overview}
              disabled={saving}
              onChange={(event) => onToggle(event.target.checked)}
              aria-label={`Show ${env.category} on Overview`}
            />
          </label>
        </td>
        <td>
          <strong>{env.category}</strong>
        </td>
        <td className="num">
          <input
            type="text"
            inputMode="decimal"
            className="budget-amount"
            value={shownAmount(env.category, env.amount)}
            disabled={saving}
            onChange={(event) => onAmountDraft(event.target.value)}
            onBlur={(event) => onAmountCommit(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") (event.target as HTMLInputElement).blur();
            }}
            aria-label={`${env.category} budgeted spend`}
          />
        </td>
      </tr>
      {env.subcategories.map((sub) => (
        <tr key={`${env.category}-${sub.subcategory}`} className="budget-sub">
          <td>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={sub.show_on_overview}
                disabled={saving}
                onChange={(event) => onToggleSub(sub.subcategory, event.target.checked)}
                aria-label={`Show ${env.category} / ${sub.subcategory} on Overview`}
              />
            </label>
          </td>
          <td>{sub.subcategory}</td>
          <td className="num">
            <input
              type="text"
              inputMode="decimal"
              className="budget-amount"
              value={shownAmount(env.category, sub.amount, sub.subcategory)}
              disabled={saving}
              onChange={(event) => onSubDraft(sub.subcategory, event.target.value)}
              onBlur={(event) => onSubCommit(sub.subcategory, event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") (event.target as HTMLInputElement).blur();
              }}
              aria-label={`${env.category} ${sub.subcategory} budgeted spend`}
            />
            <button
              type="button"
              className="btn ghost small"
              disabled={saving}
              onClick={() => onRemoveSub(sub.subcategory)}
            >
              Remove
            </button>
          </td>
        </tr>
      ))}
      <tr className="budget-add">
        <td />
        <td colSpan={2}>
          <div className="budget-add-row">
            <select
              value={add.pick}
              disabled={saving}
              onChange={(event) => onAddChange({ pick: event.target.value, name: "" })}
              aria-label={`Existing ${env.category} subcategory`}
            >
              <option value="">Existing subcategory</option>
              {(env.available_subcategories ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Or new name"
              value={add.name}
              disabled={saving}
              onChange={(event) => onAddChange({ name: event.target.value })}
              aria-label={`New ${env.category} subcategory`}
            />
            <input
              type="text"
              inputMode="decimal"
              className="budget-amount"
              placeholder="Amount"
              value={add.amount}
              disabled={saving}
              onChange={(event) => onAddChange({ amount: event.target.value })}
              aria-label={`New ${env.category} subcategory amount`}
            />
            <button className="btn small" type="button" disabled={saving} onClick={onAdd}>
              + Add subcategory budget
            </button>
          </div>
        </td>
      </tr>
    </>
  );
}
