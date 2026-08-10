import { useState } from "react";

import { api, waitForJob } from "../lib/dataSource";
import type { JobStart } from "../lib/types";

type Stage = "ingest" | "classify" | "classify-ai" | "build" | null;

/** Drives the pipeline stages from the dedicated Ingestion page. */
export function PipelineBar() {
  const [running, setRunning] = useState<Stage>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(stage: Exclude<Stage, null>, start: () => Promise<JobStart>) {
    setRunning(stage);
    setError(null);
    setMessage(null);
    try {
      const started = await start();
      if (!started.job_id) throw new Error("Server did not return a job id");
      const job = await waitForJob(started.job_id);
      if (job.status === "error") {
        setError(job.error ?? "Job failed");
      } else {
        setMessage(summarize(stage, job.result));
        window.dispatchEvent(new CustomEvent("ledger-changed"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(null);
    }
  }

  const busy = running !== null;
  return (
    <section className="pipeline-panel" aria-labelledby="pipeline-title">
      <h2 id="pipeline-title">Process statements</h2>
      <p className="muted">After adding statements, run each stage in order to refresh your ledger.</p>
      <div className="pipeline-actions">
        <button className="btn" disabled={busy} onClick={() => run("ingest", api.startIngest)}>
          {running === "ingest" ? "Ingesting…" : "Ingest"}
        </button>
        <button className="btn" disabled={busy} onClick={() => run("classify", () => api.startClassify(false))}>
          {running === "classify" ? "Classifying…" : "Classify"}
        </button>
        <button
          className="btn"
          disabled={busy}
          onClick={() => run("classify-ai", () => api.startClassify(true))}
          title="Ask the local model to propose categories for the unclassified tail"
        >
          {running === "classify-ai" ? "Asking AI…" : "Classify + AI"}
        </button>
        <button className="btn" disabled={busy} onClick={() => run("build", api.startBuild)}>
          {running === "build" ? "Building…" : "Build"}
        </button>
      </div>
      {message && <p className="pipeline-msg ok">{message}</p>}
      {error && <p className="pipeline-msg bad">{error}</p>}
    </section>
  );
}

function summarize(stage: string, result: unknown): string {
  const r = (result ?? {}) as Record<string, number | string>;
  if (stage === "ingest") return `Ingested ${r.ingested ?? 0} transactions`;
  if (stage.startsWith("classify")) {
    return `rule ${r.rule ?? 0} · merchant ${r.merchant ?? 0} · ai ${r.ai ?? 0} · manual ${r.manual ?? 0} · open ${r.open ?? 0}`;
  }
  if (stage === "build") return `Rebuilt exports · ${r.recurring_count ?? 0} recurring`;
  return "Done";
}
