import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Empty, ErrorNote, Loading, PageHeader } from "../components/ui";
import { api } from "../lib/dataSource";
import { money } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import type { InsightsChatResponse, InsightsFact, InsightsMessage } from "../lib/types";

const STARTERS = [
  "Have I really spent $53k on Amazon in the last 3 years?",
  "How does this month compare to last month?",
  "Which card looks stale?",
];

type ChatTurn = InsightsMessage & {
  facts?: InsightsFact[];
  headline?: string;
  caveat?: string | null;
};

function factSummary(facts: InsightsFact[]): string {
  const spend = [...facts].reverse().find((item) => item.result && ("net_spend" in item.result || "spend_total" in item.result));
  if (!spend?.result) {
    const tools = facts.map((item) => item.tool).filter(Boolean);
    return tools.length ? `Tools: ${tools.join(", ")}` : "No ledger facts";
  }
  const result = spend.result;
  const bits: string[] = [];
  if (typeof result.net_spend === "number") bits.push(`net ${money(result.net_spend)}`);
  else if (typeof result.spend_total === "number") bits.push(`spend ${money(result.spend_total)}`);
  if (typeof result.gross_charges === "number") bits.push(`gross ${money(result.gross_charges)}`);
  if (typeof result.credits_refunds === "number") bits.push(`credits ${money(result.credits_refunds)}`);
  if (typeof result.charge_count === "number") bits.push(`${result.charge_count} charges`);
  const period = result.period as { since?: string; until?: string } | undefined;
  if (period?.since || period?.until) bits.push(`${period.since ?? "start"} → ${period.until ?? "end"}`);
  if (typeof result.month === "string") bits.push(result.month);
  return bits.join(" · ") || "Facts used";
}

function FactCard({ fact }: { fact: InsightsFact }) {
  const result = fact.result ?? {};
  const names = Array.isArray(result.matched_names) ? (result.matched_names as { name: string; gross_charges?: number }[]) : [];
  return (
    <div className="insights-fact">
      <strong>{fact.tool ?? "rejected"}</strong>
      {fact.error && <p className="muted">{fact.error}</p>}
      {typeof result.net_spend === "number" && (
        <p>
          Net {money(result.net_spend)}
          {typeof result.gross_charges === "number" ? ` · gross ${money(result.gross_charges)}` : ""}
          {typeof result.credits_refunds === "number" ? ` · credits ${money(result.credits_refunds)}` : ""}
          {typeof result.charge_count === "number" ? ` · ${result.charge_count} charges` : ""}
        </p>
      )}
      {typeof result.spend_total === "number" && (
        <p>
          {String(result.month ?? "Month")} {money(result.spend_total)}
          {typeof result.charge_count === "number" ? ` · ${result.charge_count} charges` : ""}
        </p>
      )}
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
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const offline = status.data && (!status.data.available || !status.data.model_installed);

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
        title="Insights"
        lede="Ask the local model about this machine’s ledger. Totals are computed in Python; the model only explains those facts."
      />
      {status.loading && <Loading what="local AI status" />}
      {status.error && <ErrorNote error={status.error} />}
      {offline && (
        <p className="pipeline-msg warn">
          Local AI is offline. Start Ollama with the configured model to use Insights. The existing AI assistant setup page can download it.
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
        {busy && <p className="muted">Looking that up in the ledger…</p>}
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
