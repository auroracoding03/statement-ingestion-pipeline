import { useRef, useState } from "react";

import { PipelineBar } from "../components/PipelineBar";
import { ErrorNote, PageHeader } from "../components/ui";
import { api, productAccountKind, type UploadInspection } from "../lib/dataSource";
import { useAsync } from "../lib/useAsync";

const ISSUERS = ["American Express", "Bank of America", "Capital One", "Chase", "Wells Fargo", "Generic"];

type PendingUpload = UploadInspection & { selectedIssuer: string; selectedProduct: string; selectedCardholder: string };

function isBankUpload(upload: Pick<UploadInspection, "account_kind">): boolean {
  return upload.account_kind === "bank";
}

export function Ingestion() {
  const input = useRef<HTMLInputElement>(null);
  const productsState = useAsync(() => api.cardProducts(), []);
  const statusState = useAsync(() => api.status(), []);
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newProductByIndex, setNewProductByIndex] = useState<Record<number, string>>({});
  const [newCardholderByIndex, setNewCardholderByIndex] = useState<Record<number, string>>({});

  const products = productsState.data?.products ?? {};
  const holders = statusState.data?.cardholders ?? [];

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
          selectedCardholder: "",
        })),
      );
      setNewProductByIndex({});
      setNewCardholderByIndex({});
      setMessage("Review the detected statement details, then add the files to your inbox.");
      productsState.reload();
      statusState.reload();
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

  function changeCardholder(index: number, cardholder: string) {
    setUploads((current) =>
      current.map((upload, i) => (i === index ? { ...upload, selectedCardholder: cardholder } : upload)),
    );
  }

  function needsProduct(upload: PendingUpload): boolean {
    const configured = (products[upload.selectedIssuer] ?? []).length > 0;
    return configured || upload.confidence === "product_required" || upload.selectedIssuer === "American Express";
  }

  function effectiveCardholder(upload: PendingUpload, index: number): string {
    return (newCardholderByIndex[index] ?? "").trim() || upload.selectedCardholder;
  }

  function canCommit(): boolean {
    return uploads.every((upload, index) => {
      if (upload.needs_cardholder && !effectiveCardholder(upload, index)) return false;
      if (needsProduct(upload) && !upload.selectedProduct) return false;
      if (upload.needs_manual_details && upload.confidence !== "detected" && !upload.selectedIssuer) return false;
      return true;
    });
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
      setError(err instanceof Error ? err.message : `Could not add ${isBankUpload(upload) ? "account" : "card"} product.`);
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!uploads.length || !canCommit()) return;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const result = await api.commitUploads(
        uploads.map((upload, index) => ({
          token: upload.token,
          issuer: upload.selectedIssuer || undefined,
          product: upload.selectedProduct || undefined,
          cardholder: effectiveCardholder(upload, index) || undefined,
        })),
      );
      setUploads([]);
      setNewProductByIndex({});
      setNewCardholderByIndex({});
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
        lede="Add PDF or CSV statements, confirm only anything the app cannot identify, then ingest them. Successful files leave the queue; your ledger keeps growing."
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
        <p>Drop PDF or CSV files here. PDFs are identified from statement text; bank account CSVs from their export headers.</p>
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
            const bank = isBankUpload(upload);
            const issuerProducts = (products[upload.selectedIssuer] ?? []).filter((product) =>
              bank
                ? productAccountKind(upload.selectedIssuer, product) === "bank"
                : productAccountKind(upload.selectedIssuer, product) !== "bank",
            );
            const showProduct = needsProduct(upload);
            const showIdentity = upload.needs_manual_details && upload.confidence !== "detected";
            return (
              <article className="upload-review" key={upload.token}>
                <div>
                  <strong>{upload.name}</strong>
                  <p className="muted">{upload.message}</p>
                </div>
                {upload.needs_manual_details && (
                  <div className="upload-details">
                    {showIdentity && (
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
                    )}
                    {showIdentity && showProduct && (
                      <>
                        <label>
                          {bank ? "Account product" : "Card product"}
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
                              placeholder={bank ? "New product (e.g. Everyday Checking)" : "New product (e.g. Delta Gold)"}
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
                    {upload.needs_cardholder && (
                      <>
                        <label>
                          {bank ? "Account holder" : "Cardholder"}
                          <select
                            value={upload.selectedCardholder}
                            onChange={(event) => changeCardholder(index, event.target.value)}
                          >
                            <option value="">{bank ? "Select account holder" : "Select cardholder"}</option>
                            {holders.map((holder) => (
                              <option key={holder} value={holder}>
                                {holder}
                              </option>
                            ))}
                          </select>
                        </label>
                        <div className="toolbar" style={{ margin: 0, gap: "0.35rem" }}>
                          <input
                            type="text"
                            placeholder={bank ? "New account holder (e.g. Alex Example)" : "New cardholder (e.g. Alex Example)"}
                            value={newCardholderByIndex[index] ?? ""}
                            onChange={(event) =>
                              setNewCardholderByIndex((current) => ({
                                ...current,
                                [index]: event.target.value,
                              }))
                            }
                            disabled={busy}
                          />
                        </div>
                      </>
                    )}
                  </div>
                )}
              </article>
            );
          })}
          <button className="btn" disabled={busy || !canCommit()} onClick={() => void commit()}>
            Add to inbox
          </button>
        </section>
      )}

      <PipelineBar />
    </>
  );
}
