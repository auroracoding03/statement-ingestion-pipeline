import { useMemo, useState } from "react";

import { CategoryFields } from "../components/CategoryFields";
import { Empty, ErrorNote, Loading, PageHeader, StatusPill } from "../components/ui";
import { api, waitForJob } from "../lib/dataSource";
import { money } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import type { AiProposal } from "../lib/types";

export function AiAssistant() {
  const status = useAsync(() => api.aiStatus(), []);
  const merchants = useAsync(() => api.aiProposals("merchant"), []);
  const categories = useAsync(() => api.aiProposals("category"), []);
  const rules = useAsync(() => api.rules(), []);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saveAsRule, setSaveAsRule] = useState(false);

  const proposals = useMemo(
    () => [...(merchants.data?.items ?? []), ...(categories.data?.items ?? [])],
    [merchants.data?.items, categories.data?.items],
  );
  const categoryList = rules.data?.categories ?? [];
  const subcategoryMap = rules.data?.subcategories ?? {};

  function refresh() {
    status.reload();
    merchants.reload();
    categories.reload();
    rules.reload();
  }

  async function run(label: string, start: () => Promise<{ job_id: string }>) {
    setBusy(label);
    setError(null);
    setMessage("");
    try {
      const started = await start();
      const completed = await waitForJob(started.job_id);
      if (completed.status === "error") throw new Error(completed.error ?? "The job failed.");
      setMessage(label === "download" ? "Model download and hardware check completed." : "Analysis completed. Review the proposals below.");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  function toggle(id: string) {
    setSelected((prior) => {
      const next = new Set(prior);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function decide(decisions: { proposal_id: string; action?: "accept" | "reject" | "defer"; recommendation?: Record<string, string>; save_as_rule?: boolean }[]) {
    setBusy("decide");
    setError(null);
    try {
      const result = await api.decideAiProposals(decisions);
      setMessage(result.applied?.length ? `Applied ${result.applied.length} reviewed proposal(s).` : "Proposal status updated.");
      setSelected(new Set());
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  async function rollback() {
    if (!window.confirm("Roll back the most recent AI approval batch? Later approval batches must be rolled back first.")) return;
    setBusy("rollback");
    setError(null);
    try {
      const result = await api.rollbackAiApplication();
      setMessage(`Rolled back batch ${result.rolled_back}.`);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  const modelReady = status.data?.available && status.data?.model_installed;

  return (
    <>
      <PageHeader
        title="AI proposals"
        lede="Group statement variants, normalize merchants, and prioritize uncategorized transactions. Nothing is saved until you approve it."
      />

      <section className="ai-setup">
        <div>
          <h2>1. Local model setup</h2>
          {status.loading && <Loading what="local AI status" />}
          {status.data && (
            <p className="muted">
              <StatusPill status={status.data.available ? (status.data.gpu_resident ? "ready" : "online") : "offline"} /> {" "}
              {status.data.model} · {status.data.message}
              {status.data.size_vram > 0 && ` · ${(status.data.size_vram / 1024 ** 3).toFixed(1)} GB in VRAM`}
            </p>
          )}
          {!status.data?.available && (
            <p>
              Install <a href="https://ollama.com/download/windows" target="_blank" rel="noreferrer">Ollama for Windows</a>, then return here. The model and statement data stay on this computer.
            </p>
          )}
          <div className="review-actions">
            <button className="btn" disabled={!status.data?.available || busy !== ""} onClick={() => void run("download", () => api.startAiModelPull())}>
              {busy === "download" ? "Downloading…" : modelReady ? "Recheck GPU" : "Download recommended model"}
            </button>
            <button className="btn subtle" disabled={busy !== ""} onClick={refresh}>Refresh</button>
          </div>
          {status.data?.available && !status.data.gpu_resident && status.data.model_installed && (
            <p className="pipeline-msg bad">The model is installed but not confirmed in GPU memory. Recheck after closing other GPU-heavy apps; on an RX 6000 system Ollama should use Vulkan.</p>
          )}
        </div>
        <div>
          <h2>2. Analyze safely</h2>
          <p className="muted">Analysis batches merchant profiles instead of sending one request per transaction. Existing rules and manual decisions are preserved. Reanalyze all clears pending proposals and rebuilds the queue from the current ledger.</p>
          <div className="review-actions">
            <button className="btn" disabled={!modelReady || busy !== ""} onClick={() => void run("analyze", () => api.startAiAnalysis("incremental"))}>
              {busy === "analyze" ? "Analyzing…" : "Analyze new or changed data"}
            </button>
            <button className="btn subtle" disabled={!modelReady || busy !== ""} onClick={() => void run("analyze", () => api.startAiAnalysis("full"))}>Reanalyze all</button>
            <button className="btn danger subtle" disabled={busy !== ""} onClick={() => void rollback()}>Undo latest batch</button>
          </div>
        </div>
      </section>

      {message && <p className="pipeline-msg good">{message}</p>}
      {error && <ErrorNote error={error} />}

      <section className="ai-review-head">
        <div>
          <h2>3. Review proposals</h2>
          <p className="muted">{proposals.length} pending proposal(s), sorted as merchant identity first, then transaction categories.</p>
        </div>
        {selected.size > 0 && (
          <div className="review-actions">
            <label className="checkbox"><input type="checkbox" checked={saveAsRule} onChange={(e) => setSaveAsRule(e.target.checked)} /> Save category approvals as reusable rules</label>
            <button className="btn" disabled={busy !== ""} onClick={() => void decide([...selected].map((proposal_id) => ({ proposal_id, action: "accept", save_as_rule: saveAsRule })))}>Accept selected ({selected.size})</button>
            <button className="btn subtle" disabled={busy !== ""} onClick={() => void decide([...selected].map((proposal_id) => ({ proposal_id, action: "defer" })))}>Defer</button>
            <button className="btn danger subtle" disabled={busy !== ""} onClick={() => void decide([...selected].map((proposal_id) => ({ proposal_id, action: "reject" })))}>Reject</button>
          </div>
        )}
      </section>

      {merchants.loading || categories.loading ? <Loading what="AI proposals" /> : proposals.length === 0 ? <Empty>Nothing is waiting for review. Set up the model and run analysis after ingesting statements.</Empty> : (
        <div className="cluster-list ai-proposal-list">
          {proposals.map((proposal) => (
            <ProposalCard
              key={proposal.proposal_id}
              proposal={proposal}
              categories={categoryList}
              subcategories={subcategoryMap}
              selected={selected.has(proposal.proposal_id)}
              busy={busy !== ""}
              onToggle={toggle}
              onDecide={decide}
            />
          ))}
        </div>
      )}
    </>
  );
}

function ProposalCard({
  proposal,
  categories,
  subcategories,
  selected,
  busy,
  onToggle,
  onDecide,
}: {
  proposal: AiProposal;
  categories: string[];
  subcategories: Record<string, string[]>;
  selected: boolean;
  busy: boolean;
  onToggle: (id: string) => void;
  onDecide: (d: { proposal_id: string; action?: "accept" | "reject" | "defer"; recommendation?: Record<string, string>; save_as_rule?: boolean }[]) => void;
}) {
  const [canonical, setCanonical] = useState(proposal.recommendation.canonical ?? "");
  const [category, setCategory] = useState(proposal.recommendation.category ?? "");
  const [subcategory, setSubcategory] = useState(proposal.recommendation.subcategory ?? "");
  const merchantText = proposal.members[0] ?? proposal.recommendation.canonical ?? "merchant";
  const isMerchant = proposal.kind === "merchant";

  function lookup() {
    const query = `${merchantText} merchant`;
    if (window.confirm(`Open a web search for “${query}”? Only this merchant string will leave the app.`)) {
      window.open(`https://www.google.com/search?q=${encodeURIComponent(query)}`, "_blank", "noopener,noreferrer");
    }
  }

  const recommendation: Record<string, string> = isMerchant
    ? { canonical, category, subcategory }
    : { category, subcategory };
  const canApprove = isMerchant ? Boolean(canonical.trim()) : Boolean(category.trim());
  return (
    <article className="cluster ai-proposal">
      <div className="cluster-head">
        <label className="checkbox"><input type="checkbox" checked={selected} onChange={() => onToggle(proposal.proposal_id)} /> Select</label>
        <strong>{isMerchant ? "Merchant normalization" : "Category suggestion"}</strong>
        <StatusPill status={proposal.confidence} />
        <span className="muted">{proposal.evidence.txn_count ?? proposal.txn_ids.length} txn · {money(proposal.evidence.total_amount ?? 0)}</span>
      </div>
      <p className="muted" style={{ marginTop: "0.5rem" }}>{proposal.evidence.reason || "Local model proposal"}{proposal.evidence.ambiguous ? " · flagged ambiguous" : ""}</p>
      <div>{proposal.members.map((member) => <span key={member} className="tag">{member}</span>)}</div>
      <div className="cluster-form">
        {isMerchant && (
          <input value={canonical} onChange={(e) => setCanonical(e.target.value)} aria-label="Canonical merchant" placeholder="Canonical brand name" />
        )}
        <CategoryFields
          categories={categories}
          subcategories={subcategories}
          category={category}
          subcategory={subcategory}
          requiredCategory={!isMerchant}
          categoryLabel={isMerchant ? "Category (optional)" : "Category"}
          onCategoryChange={(nextCategory, nextSub) => {
            setCategory(nextCategory);
            setSubcategory(nextSub);
          }}
          onPairChange={(nextCategory, nextSub) => {
            setCategory(nextCategory);
            setSubcategory(nextSub);
          }}
        />
        <button className="btn" disabled={busy || !canApprove} onClick={() => onDecide([{ proposal_id: proposal.proposal_id, action: "accept", recommendation }])}>Approve</button>
        <button className="btn subtle" disabled={busy} onClick={() => onDecide([{ proposal_id: proposal.proposal_id, action: "defer" }])}>Defer</button>
        <button className="btn danger subtle" disabled={busy} onClick={() => onDecide([{ proposal_id: proposal.proposal_id, action: "reject" }])}>Reject</button>
        {isMerchant && <button className="btn subtle" disabled={busy} onClick={lookup}>Look up</button>}
      </div>
    </article>
  );
}
