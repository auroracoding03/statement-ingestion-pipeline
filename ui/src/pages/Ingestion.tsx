import { useRef, useState } from "react";

import { PipelineBar } from "../components/PipelineBar";
import { ErrorNote, PageHeader } from "../components/ui";
import { api, type UploadInspection } from "../lib/dataSource";
import { useAsync } from "../lib/useAsync";

const ISSUERS = ["American Express", "Bank of America", "Capital One", "Chase", "Wells Fargo", "Generic"];

type PendingUpload = UploadInspection & { selectedIssuer: string; selectedProduct: string };

export function Ingestion() {
  const input = useRef<HTMLInputElement>(null);
  const productsState = useAsync(() => api.cardProducts(), []);
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newProductByIndex, setNewProductByIndex] = useState<Record<number, string>>({});

  const products = productsState.data?.products ?? {};

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
      setNewProductByIndex({});
      setMessage("Review the detected statement details, then add the files to your inbox.");
      productsState.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not inspect the selected files.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  function changeIssuer(index: number, issuer: string) {
    setUploads((current) =>
      current.map((upload, i) => {
        if (i !== index) return upload;
        const allowed = products[issuer] ?? [];
        const keepProduct = allowed.includes(upload.selectedProduct) ? upload.selectedProduct : "";
        return { ...upload, selectedIssuer: issuer, selectedProduct: keepProduct };
      }),
    );
  }

  function changeProduct(index: number, product: string) {
    setUploads((current) =>
      current.map((upload, i) => (i === index ? { ...upload, selectedProduct: product } : upload)),
    );
  }

  function needsProduct(upload: PendingUpload): boolean {
    const configured = (products[upload.selectedIssuer] ?? []).length > 0;
    return configured || upload.confidence === "product_required" || upload.selectedIssuer === "American Express";
  }

  async function addProduct(index: number) {
    const upload = uploads[index];
    const label = (newProductByIndex[index] ?? "").trim();
    if (!upload?.selectedIssuer || !label) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.addCardProduct(upload.selectedIssuer, label);
      productsState.reload();
      // Prefer the returned vocab immediately so the new option is selectable.
      const allowed = result.products[upload.selectedIssuer] ?? [];
      if (allowed.includes(label)) {
        changeProduct(index, label);
      }
      setNewProductByIndex((current) => ({ ...current, [index]: "" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add card product.");
    } finally {
      setBusy(false);
    }
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
      setNewProductByIndex({});
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
        <input
          ref={input}
          type="file"
          multiple
          accept=".csv,.pdf"
          hidden
          onChange={(event) => void inspect(event.target.files ?? [])}
        />
        <button className="btn" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Inspecting…" : "Choose statements"}
        </button>
      </section>

      {message && <p className="pipeline-msg ok">{message}</p>}
      {error && <ErrorNote error={error} />}
      {productsState.error && <ErrorNote error={productsState.error} />}

      {uploads.length > 0 && (
        <section className="ingestion-queue" aria-labelledby="statement-queue-title">
          <h2 id="statement-queue-title">Ready to add</h2>
          {uploads.map((upload, index) => {
            const issuerProducts = products[upload.selectedIssuer] ?? [];
            const showProduct = needsProduct(upload);
            return (
              <article className="upload-review" key={upload.token}>
                <div>
                  <strong>{upload.name}</strong>
                  <p className="muted">{upload.message}</p>
                </div>
                {upload.needs_manual_details && (
                  <div className="upload-details">
                    <label>
                      Issuer
                      <select
                        value={upload.selectedIssuer}
                        onChange={(event) => changeIssuer(index, event.target.value)}
                        disabled={upload.confidence === "product_required" && Boolean(upload.issuer)}
                      >
                        <option value="">Select issuer</option>
                        {ISSUERS.map((issuer) => (
                          <option key={issuer} value={issuer}>
                            {issuer}
                          </option>
                        ))}
                      </select>
                    </label>
                    {showProduct && (
                      <>
                        <label>
                          Card product
                          <select
                            value={upload.selectedProduct}
                            onChange={(event) => changeProduct(index, event.target.value)}
                            disabled={!upload.selectedIssuer}
                          >
                            <option value="">Select product</option>
                            {issuerProducts.map((product) => (
                              <option key={product} value={product}>
                                {product}
                              </option>
                            ))}
                          </select>
                        </label>
                        {upload.selectedIssuer && (
                          <div className="toolbar" style={{ margin: 0, gap: "0.35rem" }}>
                            <input
                              type="text"
                              placeholder="New product (e.g. Delta Gold)"
                              value={newProductByIndex[index] ?? ""}
                              onChange={(event) =>
                                setNewProductByIndex((current) => ({
                                  ...current,
                                  [index]: event.target.value,
                                }))
                              }
                              disabled={busy}
                            />
                            <button
                              className="btn small"
                              type="button"
                              disabled={busy || !(newProductByIndex[index] ?? "").trim()}
                              onClick={() => void addProduct(index)}
                            >
                              Add product
                            </button>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </article>
            );
          })}
          <button className="btn" disabled={busy} onClick={() => void commit()}>
            Add to inbox
          </button>
        </section>
      )}

      <PipelineBar />
    </>
  );
}
