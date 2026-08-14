import { useState } from "react";

import { JobProgressBar } from "./ui";
import { api, waitForJob } from "../lib/dataSource";
import type { JobProgress, JobStart } from "../lib/types";

type Stage = "ingest" | "classify" | "classify-ai" | "build" | null;

const STAGE_LABEL: Record<Exclude<Stage, null>, string> = {
  ingest: "Ingesting statements…",
  classify: "Classifying…",
  "classify-ai": "Classify + AI…",
  build: "Building exports…",
};

/** Drives the pipeline stages from the dedicated Ingestion page. */
export function PipelineBar() {
  const [running, setRunning] = useState<Stage>(null);
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(stage: Exclude<Stage, null>, start: () => Promise<JobStart>) {
    setRunning(stage);
    setError(null);
    setMessage(null);
    setProgress(null);
    try {
      const started = await start();
      if (!started.job_id) throw new Error("Server did not return a job id");
      const job = await waitForJob(started.job_id, (tick) => {
        if (tick.progress) setProgress(tick.progress);
      });
      if (job.status === "error") {
        const details = Array.isArray((job.result as { details?: unknown } | null)?.details)
          ? ((job.result as { details: string[] }).details as string[])
          : [];
        const detailText = details.length ? ` ${details.join(" | ")}` : "";
        setError(`${job.error ?? "Job failed"}${detailText}`);
      } else {
        setMessage(summarize(stage, job.result));
        if (stage === "ingest") {
          const failed = ingestFailures(job.result);
          if (failed.length) setError(failed.join(" | "));
        }
        window.dispatchEvent(new CustomEvent("ledger-changed"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(null);
      setProgress(null);
    }
  }

  const busy = running !== null;
  return (
    <section className="pipeline-panel" aria-labelledby="pipeline-title">
      <h2 id="pipeline-title">Process statements</h2>
      <p className="muted">
        Ingest adds new transactions to your ledger, then moves successful files out of the queue.
        Classify applies your rules to anything still uncategorized.
      </p>
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
      {running && (
        <JobProgressBar
          label={progress?.message || STAGE_LABEL[running]}
          current={progress?.current}
          total={progress?.total}
        />
      )}
      {message && <p className="pipeline-msg ok">{message}</p>}
      {error && <p className="pipeline-msg bad">{error}</p>}
    </section>
  );
}

function ingestFailures(result: unknown): string[] {
  const r = (result ?? {}) as { failed?: unknown; details?: unknown };
  if (Array.isArray(r.failed) && r.failed.every((item) => typeof item === "string")) {
    return r.failed as string[];
  }
  if (Array.isArray(r.details) && r.details.every((item) => typeof item === "string")) {
    return r.details as string[];
  }
  return [];
}

function summarize(stage: string, result: unknown): string {
  const r = (result ?? {}) as Record<string, number | string | string[]>;
  if (stage === "ingest") {
    const ingested = Number(r.ingested ?? 0);
    const total = Number(r.total ?? 0);
    const archived = Array.isArray(r.archived) ? r.archived.length : Number(r.archived ?? 0);
    const failed = ingestFailures(result).length;
    if (typeof r.message === "string" && r.message.trim()) return r.message;
    const parts: string[] = [];
    if (ingested > 0) {
      parts.push(`Ingested ${ingested} new transactions (${total} total in ledger)`);
    } else if (total > 0) {
      parts.push(`No new transactions (${total} already in ledger)`);
    } else {
      parts.push("Ingested 0 transactions");
    }
    if (archived > 0) parts.push(`processed ${archived} statements`);
    if (failed > 0) parts.push(`${failed} need attention`);
    return parts.join("; ");
  }
  if (stage.startsWith("classify")) {
    return `rule ${r.rule ?? 0} · merchant ${r.merchant ?? 0} · ai ${r.ai ?? 0} · manual ${r.manual ?? 0} · open ${r.open ?? 0}`;
  }
  if (stage === "build") return `Rebuilt exports · ${r.recurring_count ?? 0} recurring`;
  return "Done";
}
