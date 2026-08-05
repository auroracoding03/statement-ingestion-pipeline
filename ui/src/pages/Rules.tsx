import { useState } from "react";

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { useAsync } from "../lib/useAsync";

export function Rules() {
  const { data, loading, error, reload } = useAsync(() => api.rules(), []);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [form, setForm] = useState({
    kind: "canonical" as "canonical" | "regex",
    value: "",
    category: "",
    subcategory: "",
  });

  async function addRule() {
    if (!form.value.trim() || !form.category.trim()) return;
    setSaveError(null);
    try {
      await api.saveRule({
        [form.kind === "canonical" ? "merchant_canonical" : "merchant_regex"]: form.value.trim(),
        category: form.category.trim(),
        subcategory: form.subcategory.trim(),
      });
      setForm({ ...form, value: "", category: "", subcategory: "" });
      reload();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <PageHeader
        title="Classification rules"
        lede="Ordered rules from config/rules.yaml. The first match wins, and canonical merchant rules are checked before regex ones."
      />

      <div className="panel" style={{ marginBottom: "1.5rem" }}>
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
          <input
            type="text"
            placeholder="Category"
            value={form.category}
            onChange={(e) => setForm({ ...form, category: e.target.value })}
          />
          <input
            type="text"
            placeholder="Subcategory"
            value={form.subcategory}
            onChange={(e) => setForm({ ...form, subcategory: e.target.value })}
          />
          <button className="btn" onClick={addRule}>
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
