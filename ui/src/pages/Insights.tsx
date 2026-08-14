import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Empty, ErrorNote, JobProgressBar, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { money } from "../lib/format";
import { hashHref } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import type { InsightsChatResponse, InsightsFact, InsightsMessage } from "../lib/types";

const STARTERS = [
  "Have I really spent $53k on Amazon in the last 3 years?",
  "How does this month compare to last month?",
  "Which card looks stale?",
  "Based on Travel spend to date this year, how much can I spend in the remaining months and stay on budget?",
  "Which budget envelopes are over YTD?",
  "What did I spend on the beach trip?",
];

type ChatTurn = InsightsMessage & {
  facts?: InsightsFact[];
  headline?: string;
  caveat?: string | null;
};

function factSummary(facts: InsightsFact[]): string {
  const spend = [...facts].reverse().find(
    (item) =>
      item.result &&
      ("net_spend" in item.result ||
        "spend_total" in item.result ||
        "actual" in item.result ||
        "over_count" in item.result ||
        "missing_count" in item.result),
  );
  if (!spend?.result) {
    const tools = facts.map((item) => item.tool).filter(Boolean);
    return tools.length ? `Tools: ${tools.join(", ")}` : "No ledger facts";
  }
  const result = spend.result;
  const bits: string[] = [];
  if (typeof result.actual === "number" && typeof result.horizon_budget === "number") {
    bits.push(`spent ${money(result.actual)} of ${money(result.horizon_budget)}`);
    if (typeof result.remaining === "number") bits.push(`left ${money(result.remaining)}`);
    if (typeof result.pct_used === "number") bits.push(`${result.pct_used}% used`);
  } else if (typeof result.net_spend === "number") bits.push(`net ${money(result.net_spend)}`);
  else if (typeof result.spend_total === "number") bits.push(`spend ${money(result.spend_total)}`);
  if (typeof result.over_count === "number") bits.push(`${result.over_count} over`);
  if (typeof result.missing_count === "number") bits.push(`${result.missing_count} missing bills`);
  if (typeof result.gross_charges === "number") bits.push(`gross ${money(result.gross_charges)}`);
  if (typeof result.credits_refunds === "number") bits.push(`credits ${money(result.credits_refunds)}`);
  if (typeof result.charge_count === "number") bits.push(`${result.charge_count} charges`);
  const period = result.period as { since?: string; until?: string } | undefined;
  if (period?.since || period?.until) bits.push(`${period.since ?? "start"} → ${period.until ?? "end"}`);
  if (typeof result.month === "string") bits.push(result.month);
  return bits.join(" · ") || "Facts used";
}

type EnvelopeRow = {
  category?: string;
  subcategory?: string | null;
  actual?: number;
  window_budget?: number;
  remaining?: number;
  over_budget?: boolean;
};

type MatchedTag = { id?: string; label?: string };
type BillRow = { bill?: string; status?: string };

function envelopeLabel(row: EnvelopeRow): string {
  return row.subcategory ? `${row.category ?? ""} / ${row.subcategory}` : String(row.category ?? "envelope");
}

function FactCard({ fact }: { fact: InsightsFact }) {
  const result = fact.result ?? {};
  const names = Array.isArray(result.matched_names) ? (result.matched_names as { name: string; gross_charges?: number }[]) : [];
  const envelopes = Array.isArray(result.rows) && fact.tool === "budget_status" ? (result.rows as EnvelopeRow[]) : [];
  const bills = Array.isArray(result.rows) && fact.tool === "expected_bills" ? (result.rows as BillRow[]) : [];
  const tags = Array.isArray(result.matched_tags) ? (result.matched_tags as MatchedTag[]) : [];
  return (
    <div className="insights-fact">
      <strong>{fact.tool ?? "rejected"}</strong>
      {fact.error && <p className="muted">{fact.error}</p>}
      {typeof result.actual === "number" && (
        <p>
          Actual {money(result.actual)}
          {typeof result.horizon_budget === "number" ? ` · horizon ${money(result.horizon_budget)}` : ""}
          {typeof result.remaining === "number" ? ` · remaining ${money(result.remaining)}` : ""}
          {typeof result.pct_used === "number" ? ` · ${result.pct_used}% used` : ""}
          {result.budget_set === false ? " · no envelope set" : ""}
        </p>
      )}
      {typeof result.net_spend === "number" && (
        <p>
          Net {money(result.net_spend)}
          {typeof result.gross_charges === "number" ? ` · gross ${money(result.gross_charges)}` : ""}
          {typeof result.credits_refunds === "number" ? ` · credits ${money(result.credits_refunds)}` : ""}
          {typeof result.charge_count === "number" ? ` · ${result.charge_count} charges` : ""}
          {typeof result.review_count === "number" ? ` · ${result.review_count} need review` : ""}
        </p>
      )}
      {typeof result.spend_total === "number" && (
        <p>
          {String(result.month ?? "Month")} {money(result.spend_total)}
          {typeof result.charge_count === "number" ? ` · ${result.charge_count} charges` : ""}
        </p>
      )}
      {envelopes.length > 0 && (
        <ul>
          {envelopes.map((row) => (
            <li key={envelopeLabel(row)}>
              {envelopeLabel(row)}
              {typeof row.actual === "number" && typeof row.window_budget === "number"
                ? ` · ${money(row.actual)} of ${money(row.window_budget)}`
                : ""}
              {row.over_budget ? " · over" : " · under"}
            </li>
          ))}
        </ul>
      )}
      {bills.length > 0 && (
        <p>
          {bills.filter((row) => row.status === "seen").length} seen · {bills.filter((row) => row.status === "missing").length} missing
          {bills.some((row) => row.status === "missing")
            ? ` (${bills.filter((row) => row.status === "missing").map((row) => row.bill).join(", ")})`
            : ""}
        </p>
      )}
      {tags.length > 0 && <p className="muted">{tags.map((item) => item.label || item.id).join(", ")}</p>}
      {names.length > 0 && (
        <p className="muted">{names.map((item) => item.name).join(", ")}</p>
      )}
    </div>
  );
}

export function Insights() {
  const status = useAsync(() => api.aiStatus(), []);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const daemonDown = Boolean(status.data && !status.data.available);
  const modelMissing = Boolean(status.data?.available && !status.data.model_installed);
  const offline = daemonDown || modelMissing;

  async function startOllama() {
    if (starting) return;
    setStarting(true);
    setError(null);
    try {
      await api.startOllama();
      status.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }

  async function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    const history: InsightsMessage[] = [
      ...turns.map((item) => ({ role: item.role, content: item.content })),
      { role: "user", content: question },
    ];
    setTurns((prior) => [...prior, { role: "user", content: question }]);
    setDraft("");
    setBusy(true);
    setError(null);
    try {
      const result: InsightsChatResponse = await api.insightsChat(history.slice(-8));
      setTurns((prior) => [
        ...prior,
        {
          role: "assistant",
          content: result.reply,
          facts: result.facts,
          headline: result.headline,
          caveat: result.caveat,
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      requestAnimationFrame(() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }));
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(draft);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(draft);
    }
  }

  return (
    <>
      <PageHeader
        title="Ask the ledger"
        lede="Ask the local model about this machine’s ledger. Totals are computed in Python; the model only explains those facts."
      />
      {status.loading && <Loading what="local AI status" />}
      {status.error && <ErrorNote error={status.error} />}
      {daemonDown && (
        <p className="pipeline-msg warn">
          Local AI is offline because Ollama is not running.
          <button className="btn small" type="button" style={{ marginLeft: "0.75rem" }} disabled={starting} onClick={() => void startOllama()}>
            {starting ? "Starting…" : "Start Ollama"}
          </button>
        </p>
      )}
      {modelMissing && (
        <p className="pipeline-msg warn">
          Ollama is running, but the configured model is not installed.{" "}
          <a href={hashHref("/ai-assistant")}>Download it on the AI proposals page</a>.
        </p>
      )}
      {error && <ErrorNote error={error} />}

      <div className="insights-chat" ref={scroller}>
        {turns.length === 0 && !busy && (
          <Empty>
            Try a starter, or ask something like Amazon spend over a date range. Chat is not saved; reload clears it.
          </Empty>
        )}
        {turns.map((turn, index) => (
          <article key={`${turn.role}-${index}`} className={`insights-bubble insights-${turn.role}`}>
            <span className="insights-role">{turn.role === "user" ? "You" : "Ledger"}</span>
            <p>{turn.content}</p>
            {turn.role === "assistant" && turn.facts && turn.facts.length > 0 && (
              <details className="insights-facts">
                <summary>Facts used · {factSummary(turn.facts)}</summary>
                {turn.headline && turn.headline !== turn.content && <p className="muted">{turn.headline}</p>}
                {turn.facts.map((fact, factIndex) => (
                  <FactCard key={`${fact.tool}-${factIndex}`} fact={fact} />
                ))}
              </details>
            )}
          </article>
        ))}
        {busy && <JobProgressBar label="Looking that up in the ledger…" />}
      </div>

      <div className="insights-starters">
        {STARTERS.map((prompt) => (
          <button key={prompt} type="button" className="btn subtle small" disabled={busy || Boolean(offline)} onClick={() => void send(prompt)}>
            {prompt}
          </button>
        ))}
      </div>

      <form className="insights-composer" onSubmit={onSubmit}>
        <textarea
          rows={3}
          value={draft}
          maxLength={500}
          disabled={busy || Boolean(offline)}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about spend, a merchant, a month, or a card…"
        />
        <button className="btn" type="submit" disabled={busy || Boolean(offline) || !draft.trim()}>
          Ask
        </button>
      </form>
    </>
  );
}
