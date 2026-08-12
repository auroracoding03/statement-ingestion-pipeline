/**
 * Single data access layer with two backends.
 *
 * live   - talks to the local FastAPI server, full read/write
 * static - reads the exported ./data/*.json artifacts, read-only
 *
 * Pages never branch on mode themselves; they call these functions and use
 * `canWrite` to decide whether to render mutating controls.
 */
import type {
  AiProposal,
  AiStatus,
  CategoryMonthly,
  ContextTag,
  Job,
  JobStart,
  Merchant,
  OverviewMonth,
  ReconciliationRow,
  RecurringRow,
  ReviewQueue,
  Rule,
  Status,
  Transaction,
  UnknownCluster,
} from "./types";

export const MODE: "live" | "static" =
  typeof __DATA_MODE__ !== "undefined" ? __DATA_MODE__ : "live";
export const canWrite = MODE === "live";

class DataError extends Error {}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* response had no JSON body */
    }
    throw new DataError(detail);
  }
  return res.json() as Promise<T>;
}

async function staticJSON<T>(name: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(`./data/${name}.json`);
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

function writeGuard(): never {
  throw new DataError("This dashboard is read-only. Run `fin serve` for the interactive UI.");
}

interface StaticSummary {
  mode: string;
  txn_count: number;
  uncategorized_count: number;
  canonical_count: number;
  unknown_merchant_count: number;
  recurring_count: number;
}

export interface UpdateStatus {
  supported: boolean;
  current_version: string;
  update_available: boolean;
  latest_version?: string;
  release_url?: string;
  message: string;
}

export interface UploadInspection {
  token: string;
  name: string;
  issuer: string | null;
  product: string | null;
  confidence: string;
  message: string;
  needs_manual_details: boolean;
}

export const api = {
  async aiStatus(warmup = false): Promise<AiStatus> {
    if (!canWrite) throw new DataError("Local AI setup is only available in the desktop app.");
    return req<AiStatus>(`/api/ai/status${warmup ? "?warmup=true" : ""}`);
  },

  async aiProposals(kind?: "merchant" | "category") {
    if (!canWrite) return { total: 0, items: [] as AiProposal[] };
    const suffix = kind ? `&kind=${kind}` : "";
    return req<{ total: number; items: AiProposal[] }>(`/api/ai/proposals?status=pending${suffix}`);
  },

  async status(): Promise<Status> {
    if (canWrite) return req<Status>("/api/status");
    const summary = await staticJSON<StaticSummary | null>("summary", null);
    return {
      ledger_exists: Boolean(summary),
      counts: { total: summary?.txn_count ?? 0, open: summary?.uncategorized_count ?? 0 },
      canonical_merchants: summary?.canonical_count ?? 0,
      unknown_merchants: summary?.unknown_merchant_count ?? 0,
      review_pending: summary?.uncategorized_count ?? 0,
      cardholders: [],
      inbox_files: [],
      duckdb: false,
      exports: true,
      ollama_available: false,
    };
  },

  async transactions(params: Record<string, string | number | boolean> = {}) {
    if (canWrite) {
      const qs = new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== "" && v !== undefined && v !== false)
          .map(([k, v]) => [k, String(v)]),
      );
      return req<{ total: number; items: Transaction[] }>(`/api/transactions?${qs}`);
    }
    const items = await staticJSON<Transaction[]>("ledger", []);
    return { total: items.length, items };
  },

  async categoriesMonthly(): Promise<CategoryMonthly[]> {
    if (canWrite) return req<CategoryMonthly[]>("/api/categories/monthly");
    return staticJSON<CategoryMonthly[]>("category_monthly", []);
  },

  async overviewMonth(params: { month?: string; cardholder?: string } = {}): Promise<OverviewMonth> {
    if (!canWrite) {
      return {
        month: params.month ?? null,
        months: [],
        cardholder: params.cardholder ?? null,
        spend_total: 0,
        prior_spend_total: null,
        spend_delta: null,
        spend_delta_pct: null,
        charge_count: 0,
        payments_and_refunds: 0,
        uncategorized_total: 0,
        uncategorized_count: 0,
        review_count: 0,
        categories: [],
        holders: [],
        large_charges: [],
        tagged: [],
        bills: [],
      };
    }
    const qs = new URLSearchParams();
    if (params.month) qs.set("month", params.month);
    if (params.cardholder) qs.set("cardholder", params.cardholder);
    const suffix = qs.toString() ? `?${qs}` : "";
    return req<OverviewMonth>(`/api/overview/month${suffix}`);
  },

  async recurring(): Promise<RecurringRow[]> {
    if (canWrite) return req<RecurringRow[]>("/api/recurring");
    return staticJSON<RecurringRow[]>("recurring", []);
  },

  async reconciliation(): Promise<ReconciliationRow[]> {
    if (canWrite) return req<ReconciliationRow[]>("/api/reconciliation");
    return staticJSON<ReconciliationRow[]>("reconciliation", []);
  },

  async merchants(): Promise<{ total: number; items: Merchant[] }> {
    if (canWrite) return req<{ total: number; items: Merchant[] }>("/api/merchants");
    const items = await staticJSON<
      { merchant: string; canonical: boolean; total: number; txn_count: number }[]
    >("merchants", []);
    return {
      total: items.length,
      items: items.map((m) => ({
        canonical: m.merchant,
        category: null,
        subcategory: null,
        aliases: [],
        txn_count: m.txn_count,
        total_amount: m.total,
      })),
    };
  },

  async unknownMerchants(threshold = 88, withAi = false) {
    if (!canWrite) return { total: 0, items: [] as UnknownCluster[], ollama_available: false };
    return req<{ total: number; items: UnknownCluster[]; ollama_available: boolean }>(
      `/api/merchants/unknown?threshold=${threshold}&with_ai=${withAi}`,
    );
  },

  async reviewQueue(): Promise<ReviewQueue> {
    if (!canWrite) {
      const items = await staticJSON<Transaction[]>("uncategorized", []);
      return { total: items.length, items, categories: [], subcategories: {} };
    }
    return req<ReviewQueue>("/api/review/queue");
  },

  async rules(): Promise<{
    categories: string[];
    subcategories: Record<string, string[]>;
    rules: Rule[];
  }> {
    if (!canWrite) return { categories: [], subcategories: {}, rules: [] };
    return req<{ categories: string[]; subcategories: Record<string, string[]>; rules: Rule[] }>("/api/rules");
  },

  async tags(): Promise<{ total: number; items: ContextTag[] }> {
    if (!canWrite) return { total: 0, items: [] };
    return req<{ total: number; items: ContextTag[] }>("/api/tags");
  },

  async updates(): Promise<UpdateStatus> {
    if (!canWrite) {
      return {
        supported: false,
        current_version: "",
        update_available: false,
        message: "Updates are available in the installed Windows application.",
      };
    }
    return req<UpdateStatus>("/api/updates");
  },

  async inspectUploads(files: FileList | File[]) {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("files", file));
    const res = await fetch("/api/uploads/inspect", { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new DataError(String(body?.detail ?? `Upload failed: ${res.status}`));
    }
    return res.json() as Promise<{ items: UploadInspection[] }>;
  },

  commitUploads(items: { token: string; issuer?: string; product?: string }[]) {
    return req<{ written: string[] }>("/api/uploads/commit", { method: "POST", body: JSON.stringify({ items }) });
  },

  async cardProducts(): Promise<{ products: Record<string, string[]> }> {
    if (!canWrite) return { products: {} };
    return req<{ products: Record<string, string[]> }>("/api/card-products");
  },

  addCardProduct(issuer: string, product: string) {
    if (!canWrite) writeGuard();
    return req<{ products: Record<string, string[]> }>("/api/card-products", {
      method: "POST",
      body: JSON.stringify({ issuer, product }),
    });
  },

  // ---------------------------------------------------------------- mutations

  submitReview(
    txnId: string,
    body: {
      category: string;
      subcategory?: string;
      tags?: string[];
      create_rule?: boolean;
      rule_scope?: string;
    },
  ) {
    if (!canWrite) writeGuard();
    return req<{
      txn_id: string;
      category: string;
      subcategory?: string;
      tags?: string[];
      rule?: unknown;
      applied_txn_ids?: string[];
    }>(`/api/review/${txnId}`, { method: "POST", body: JSON.stringify(body) });
  },

  createTag(body: { label: string; kind?: string; id?: string }) {
    if (!canWrite) writeGuard();
    return req<{ tag: ContextTag }>("/api/tags", { method: "POST", body: JSON.stringify(body) });
  },

  deleteTag(tagId: string) {
    if (!canWrite) writeGuard();
    return req<unknown>(`/api/tags/${encodeURIComponent(tagId)}`, { method: "DELETE" });
  },

  addCategory(category: string) {
    if (!canWrite) writeGuard();
    return req<{ categories: string[]; subcategories: Record<string, string[]> }>("/api/categories", {
      method: "POST",
      body: JSON.stringify({ category }),
    });
  },

  addSubcategory(category: string, subcategory: string) {
    if (!canWrite) writeGuard();
    return req<{ subcategories: Record<string, string[]> }>("/api/subcategories", {
      method: "POST",
      body: JSON.stringify({ category, subcategory }),
    });
  },

  saveMerchant(body: {
    canonical: string;
    members?: string[];
    aliases?: { regex?: string; exact?: string }[];
    category?: string | null;
    subcategory?: string | null;
    restamp?: boolean;
  }) {
    if (!canWrite) writeGuard();
    return req<unknown>("/api/merchants", { method: "POST", body: JSON.stringify(body) });
  },

  deleteMerchant(canonical: string) {
    if (!canWrite) writeGuard();
    return req<unknown>(`/api/merchants/${encodeURIComponent(canonical)}`, { method: "DELETE" });
  },

  saveRule(body: {
    merchant_regex?: string;
    merchant_canonical?: string;
    category: string;
    subcategory?: string;
  }) {
    if (!canWrite) writeGuard();
    return req<unknown>("/api/rules", { method: "POST", body: JSON.stringify(body) });
  },

  updateRule(index: number, body: { category: string; subcategory?: string }) {
    if (!canWrite) writeGuard();
    return req<unknown>(`/api/rules/${index}`, { method: "PATCH", body: JSON.stringify(body) });
  },

  deleteRule(index: number) {
    if (!canWrite) writeGuard();
    return req<unknown>(`/api/rules/${index}`, { method: "DELETE" });
  },

  startIngest() {
    if (!canWrite) writeGuard();
    return req<JobStart>("/api/ingest", { method: "POST" });
  },

  startClassify(withAi: boolean) {
    if (!canWrite) writeGuard();
    return req<JobStart>("/api/classify", {
      method: "POST",
      body: JSON.stringify({ with_ai: withAi }),
    });
  },

  startBuild() {
    if (!canWrite) writeGuard();
    return req<JobStart>("/api/build", { method: "POST" });
  },

  startAiModelPull() {
    if (!canWrite) writeGuard();
    return req<JobStart>("/api/ai/model/pull", { method: "POST" });
  },

  startAiAnalysis(mode: "full" | "incremental" = "incremental") {
    if (!canWrite) writeGuard();
    return req<JobStart>("/api/ai/analyze", { method: "POST", body: JSON.stringify({ mode }) });
  },

  decideAiProposals(
    decisions: {
      proposal_id: string;
      action?: "accept" | "reject" | "defer";
      recommendation?: Record<string, string>;
      save_as_rule?: boolean;
    }[],
  ) {
    if (!canWrite) writeGuard();
    return req<{ batch_id?: string; applied?: string[] }>("/api/ai/proposals/decide", {
      method: "POST",
      body: JSON.stringify({ decisions }),
    });
  },

  rollbackAiApplication() {
    if (!canWrite) writeGuard();
    return req<{ rolled_back: string }>("/api/ai/applications/rollback", { method: "POST" });
  },

  installUpdate() {
    if (!canWrite) writeGuard();
    return req<{ message: string }>("/api/updates/install", { method: "POST" });
  },

  job(id: string) {
    return req<Job>(`/api/jobs/${id}`);
  },

  async upload(issuer: string, product: string, files: Iterable<File>) {
    if (!canWrite) writeGuard();
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    const params = new URLSearchParams({ issuer });
    if (product.trim()) params.set("product", product.trim());
    const res = await fetch(`/api/upload?${params.toString()}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new DataError(`Upload failed: ${res.status}`);
    return res.json();
  },
};

/** Poll a background job until it settles. */
export async function waitForJob(id: string, onTick?: (job: Job) => void): Promise<Job> {
  for (;;) {
    const job = await api.job(id);
    onTick?.(job);
    if (job.status === "done" || job.status === "error") return job;
    await new Promise((r) => setTimeout(r, 700));
  }
}
