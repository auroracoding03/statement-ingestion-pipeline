import { Fragment, useMemo, useState } from "react";

import { Empty, ErrorNote, Loading, Metric, PageHeader, StatusPill } from "../components/ui";
import { api } from "../lib/dataSource";
import {
  centsToDollarInput,
  currentMonthLocal,
  dollarsToCents,
  moneyCents,
  shortDate,
  todayLocalISO,
} from "../lib/format";
import { useAsync } from "../lib/useAsync";
import type { MonthlyObligation, Obligation } from "../lib/types";

type DefForm = {
  name: string;
  category: string;
  subcategory: string;
  amount: string;
  due_day: string;
};

const EMPTY_FORM: DefForm = {
  name: "",
  category: "Housing",
  subcategory: "",
  amount: "",
  due_day: "1",
};

export function Obligations() {
  const [month, setMonth] = useState(currentMonthLocal());
  const monthView = useAsync(() => api.obligationMonth(month), [month]);
  const defs = useAsync(() => api.obligations(true), []);
  const [error, setError] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<DefForm>(EMPTY_FORM);

  function refresh() {
    monthView.reload();
    defs.reload();
  }

  async function saveDefinition() {
    setError(null);
    try {
      const expected_amount_cents = dollarsToCents(form.amount);
      const due_day = Number(form.due_day);
      const body = {
        name: form.name.trim(),
        category: form.category.trim(),
        subcategory: form.subcategory.trim(),
        expected_amount_cents,
        due_day,
      };
      if (editingId) {
        await api.updateObligation(editingId, body);
      } else {
        await api.createObligation(body);
      }
      setForm(EMPTY_FORM);
      setEditingId(null);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function startEdit(o: Obligation) {
    setEditingId(o.id);
    setForm({
      name: o.name,
      category: o.category,
      subcategory: o.subcategory || "",
      amount: centsToDollarInput(o.expected_amount_cents),
      due_day: String(o.due_day),
    });
  }

  async function deactivate(id: string) {
    setError(null);
    try {
      await api.deactivateObligation(id);
      if (editingId === id) {
        setEditingId(null);
        setForm(EMPTY_FORM);
      }
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function skip(item: MonthlyObligation) {
    setError(null);
    try {
      await api.confirmObligation(month, item.obligation_id, { status: "skipped" });
      setConfirmingId(null);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function reset(item: MonthlyObligation) {
    setError(null);
    try {
      await api.resetObligation(month, item.obligation_id);
      setConfirmingId(null);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const summary = monthView.data;

  return (
    <>
      <PageHeader
        title="Obligations"
        lede="Track predictable monthly expenses that never appear on a credit-card statement."
      />

      <p className="callout">
        Use this page only for expenses that do not appear in imported credit-card statements.
        Expected amounts do not count as spending until you confirm payment.
      </p>

      {error && <ErrorNote error={error} />}

      <div className="toolbar">
        <label className="muted" htmlFor="obl-month">
          Month
        </label>
        <input
          id="obl-month"
          type="month"
          value={month}
          onChange={(e) => {
            setMonth(e.target.value);
            setConfirmingId(null);
          }}
        />
      </div>

      {monthView.loading && <Loading what="monthly obligations" />}
      {monthView.error && <ErrorNote error={monthView.error} />}

      {summary && (
        <div className="metrics">
          <Metric label="Expected" value={moneyCents(summary.expected_total_cents)} />
          <Metric label="Paid" value={moneyCents(summary.paid_total_cents)} />
          <Metric label="Outstanding" value={moneyCents(summary.outstanding_total_cents)} />
          <Metric label="Overdue" value={summary.overdue_count} />
        </div>
      )}

      <h2>Monthly checklist</h2>
      {!monthView.loading && (summary?.items.length ?? 0) === 0 && (
        <Empty>No active obligations. Add one below.</Empty>
      )}

      {(summary?.items.length ?? 0) > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Due</th>
                <th>Category</th>
                <th className="num">Expected</th>
                <th>Status</th>
                <th className="num">Actual</th>
                <th>Paid date</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {summary!.items.map((item) => (
                <Fragment key={item.obligation_id}>
                  <tr>
                    <td>
                      <strong>{item.name}</strong>
                      {item.amount_changed && (
                        <span className="tag" title="Actual differs from expected">
                          amount changed
                        </span>
                      )}
                    </td>
                    <td>{shortDate(item.due_date)}</td>
                    <td>
                      {[item.category, item.subcategory].filter(Boolean).join(" / ")}
                    </td>
                    <td className="num">{moneyCents(item.expected_amount_cents)}</td>
                    <td>
                      <StatusPill status={item.status} />
                    </td>
                    <td className="num">{moneyCents(item.actual_amount_cents)}</td>
                    <td>{shortDate(item.paid_date)}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="btn small"
                          onClick={() =>
                            setConfirmingId(
                              confirmingId === item.obligation_id ? null : item.obligation_id,
                            )
                          }
                        >
                          Confirm paid
                        </button>
                        <button className="btn subtle small" onClick={() => void skip(item)}>
                          Skip
                        </button>
                        <button className="btn subtle small" onClick={() => void reset(item)}>
                          Reset
                        </button>
                      </div>
                    </td>
                  </tr>
                  {confirmingId === item.obligation_id && (
                    <tr className="inline-form-row">
                      <td colSpan={8}>
                        <ConfirmPaidForm
                          item={item}
                          month={month}
                          onCancel={() => setConfirmingId(null)}
                          onSaved={() => {
                            setConfirmingId(null);
                            refresh();
                          }}
                          onError={setError}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Manage obligations</h2>
      <div className="panel obligation-form">
        <div className="form-grid">
          <label>
            Name
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              maxLength={100}
            />
          </label>
          <label>
            Expected amount ($)
            <input
              type="text"
              inputMode="decimal"
              placeholder="2100.00"
              value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })}
            />
          </label>
          <label>
            Due day (1–28)
            <input
              type="number"
              min={1}
              max={28}
              value={form.due_day}
              onChange={(e) => setForm({ ...form, due_day: e.target.value })}
            />
          </label>
          <label>
            Category
            <input
              type="text"
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value })}
            />
          </label>
          <label>
            Subcategory
            <input
              type="text"
              value={form.subcategory}
              onChange={(e) => setForm({ ...form, subcategory: e.target.value })}
            />
          </label>
        </div>
        <div className="row-actions" style={{ marginTop: "0.9rem" }}>
          <button className="btn" onClick={() => void saveDefinition()}>
            {editingId ? "Save changes" : "Add obligation"}
          </button>
          {editingId && (
            <button
              className="btn subtle"
              onClick={() => {
                setEditingId(null);
                setForm(EMPTY_FORM);
              }}
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      {defs.loading && <Loading what="definitions" />}
      {defs.error && <ErrorNote error={defs.error} />}
      {(defs.data?.items.length ?? 0) === 0 && !defs.loading && (
        <Empty>No active obligation definitions yet.</Empty>
      )}

      {(defs.data?.items.length ?? 0) > 0 && (
        <div className="table-wrap" style={{ marginTop: "1rem" }}>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th className="num">Expected</th>
                <th className="num">Due day</th>
                <th>Category</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {defs.data!.items.map((o) => (
                <tr key={o.id}>
                  <td>{o.name}</td>
                  <td className="num">{moneyCents(o.expected_amount_cents)}</td>
                  <td className="num">{o.due_day}</td>
                  <td>{[o.category, o.subcategory].filter(Boolean).join(" / ")}</td>
                  <td>
                    <div className="row-actions">
                      <button className="btn subtle small" onClick={() => startEdit(o)}>
                        Edit
                      </button>
                      <button className="btn danger small" onClick={() => void deactivate(o.id)}>
                        Deactivate
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function ConfirmPaidForm({
  item,
  month,
  onCancel,
  onSaved,
  onError,
}: {
  item: MonthlyObligation;
  month: string;
  onCancel: () => void;
  onSaved: () => void;
  onError: (msg: string) => void;
}) {
  const defaultPaidDate = useMemo(() => {
    if (month === currentMonthLocal()) return todayLocalISO();
    return `${month}-01`;
  }, [month]);

  const [amount, setAmount] = useState(centsToDollarInput(item.expected_amount_cents));
  const [paidDate, setPaidDate] = useState(item.paid_date ?? defaultPaidDate);
  const [note, setNote] = useState(item.note || "");
  const [saving, setSaving] = useState(false);

  async function submit() {
    setSaving(true);
    onError("");
    try {
      const actual_amount_cents = dollarsToCents(amount);
      await api.confirmObligation(month, item.obligation_id, {
        status: "paid",
        actual_amount_cents,
        paid_date: paidDate,
        note,
      });
      onSaved();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="inline-confirm">
      <strong>Confirm payment — {item.name}</strong>
      <div className="form-grid compact">
        <label>
          Actual amount ($)
          <input type="text" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label>
          Paid date
          <input type="date" value={paidDate} onChange={(e) => setPaidDate(e.target.value)} />
        </label>
        <label>
          Note (optional)
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <div className="row-actions">
        <button className="btn" disabled={saving} onClick={() => void submit()}>
          {saving ? "Saving…" : "Save payment"}
        </button>
        <button className="btn subtle" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
