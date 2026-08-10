import { useRef, useState } from "react";

import { PipelineBar } from "../components/PipelineBar";
import { ErrorNote, PageHeader } from "../components/ui";
import { api, type UploadInspection } from "../lib/dataSource";

const ISSUERS = ["American Express", "Bank of America", "Capital One", "Chase", "Wells Fargo", "Generic"];

type PendingUpload = UploadInspection & { selectedIssuer: string; selectedProduct: string };

export function Ingestion() {
  const input = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function inspect(files: FileList | File[]) {
    if (!files.length) return;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const result = await api.inspectUploads(files);
      setUploads(
        result.items.map((item) => ({
          ...item,
          selectedIssuer: item.issuer ?? "",
          selectedProduct: item.product ?? "",
        })),
      );
      setMessage("Review the detected statement details, then add the files to your inbox.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not inspect the selected files.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  function changeUpload(index: number, field: "selectedIssuer" | "selectedProduct", value: string) {
    setUploads((current) => current.map((upload, i) => (i === index ? { ...upload, [field]: value } : upload)));
  }

  async function commit() {
    if (!uploads.length) return;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const result = await api.commitUploads(
        uploads.map((upload) => ({
          token: upload.token,
          issuer: upload.selectedIssuer || undefined,
          product: upload.selectedProduct || undefined,
        })),
      );
      setUploads([]);
      setMessage(`Added ${result.written.length} statement${result.written.length === 1 ? "" : "s"} to your inbox.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the selected statements.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Ingest statements"
        lede="Add PDF or CSV statements, confirm only anything the app cannot identify, then process them into your ledger."
      />
      <section
        className="ingestion-dropzone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          void inspect(event.dataTransfer.files);
        }}
      >
        <h2>Add statements</h2>
        <p>Drop PDF or CSV files here. PDFs are identified automatically from statement text.</p>
        <input ref={input} type="file" multiple accept=".csv,.pdf" hidden onChange={(event) => void inspect(event.target.files ?? [])} />
        <button className="btn" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Inspecting…" : "Choose statements"}
        </button>
      </section>

      {message && <p className="pipeline-msg ok">{message}</p>}
      {error && <ErrorNote error={error} />}

      {uploads.length > 0 && (
        <section className="ingestion-queue" aria-labelledby="statement-queue-title">
          <h2 id="statement-queue-title">Ready to add</h2>
          {uploads.map((upload, index) => (
            <article className="upload-review" key={upload.token}>
              <div>
                <strong>{upload.name}</strong>
                <p className="muted">{upload.message}</p>
              </div>
              {upload.needs_manual_details && (
                <div className="upload-details">
                  <label>
                    Issuer
                    <select value={upload.selectedIssuer} onChange={(event) => changeUpload(index, "selectedIssuer", event.target.value)}>
                      <option value="">Select issuer</option>
                      {ISSUERS.map((issuer) => <option key={issuer}>{issuer}</option>)}
                    </select>
                  </label>
                  {(upload.selectedIssuer === "American Express" || upload.confidence === "product_required") && (
                    <label>
                      Card product
                      <input value={upload.selectedProduct} onChange={(event) => changeUpload(index, "selectedProduct", event.target.value)} placeholder="e.g. Platinum" />
                    </label>
                  )}
                </div>
              )}
            </article>
          ))}
          <button className="btn" disabled={busy} onClick={() => void commit()}>
            Add to inbox
          </button>
        </section>
      )}

      <PipelineBar />
    </>
  );
}
