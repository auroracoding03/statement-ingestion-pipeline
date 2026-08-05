import { useRef, useState } from "react";

import { api, waitForJob } from "../lib/dataSource";
import type { JobStart } from "../lib/types";

type Stage = "ingest" | "classify" | "classify-ai" | "build" | null;

/**
 * Drives the pipeline stages from the browser. Each action starts a background
 * job on the server and polls until it settles, so an AI pass can run long
 * without holding a request open.
 */
export function PipelineBar() {
  const [running, setRunning] = useState<Stage>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [card, setCard] = useState("chase");

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

  async function onUpload(files: FileList | null) {
    if (!files?.length) return;
    setError(null);
    try {
      const result = (await api.upload(card, files)) as { written: string[] };
      setMessage(`Uploaded ${result.written.length} file(s) to inbox/${card}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  const busy = running !== null;

  return (
    <div className="pipeline-bar">
      <div className="pipeline-actions">
        <label className="upload">
          <input
            ref={fileInput}
            type="file"
            multiple
            accept=".csv,.pdf"
            hidden
            onChange={(e) => onUpload(e.target.files)}
          />
          <span className="btn ghost">Add statements</span>
        </label>
        <input
          className="card-input"
          value={card}
          onChange={(e) => setCard(e.target.value)}
          aria-label="Card folder"
          title="Inbox subfolder (issuer / card)"
        />

        <span className="divider" />

        <button className="btn" disabled={busy} onClick={() => run("ingest", api.startIngest)}>
          {running === "ingest" ? "Ingesting…" : "Ingest"}
        </button>
        <button
          className="btn"
          disabled={busy}
          onClick={() => run("classify", () => api.startClassify(false))}
        >
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
    </div>
  );
}

function summarize(stage: string, result: unknown): string {
  const r = (result ?? {}) as Record<string, number | string>;
  if (stage === "ingest") return `Ingested ${r.ingested ?? 0} transactions`;
  if (stage.startsWith("classify")) {
    return `rule ${r.rule ?? 0} · merchant ${r.merchant ?? 0} · ai ${r.ai ?? 0} · manual ${
      r.manual ?? 0
    } · open ${r.open ?? 0}`;
  }
  if (stage === "build") return `Rebuilt exports · ${r.recurring_count ?? 0} recurring`;
  return "Done";
}
