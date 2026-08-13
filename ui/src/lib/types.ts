export type ClassifiedBy = "rule" | "merchant" | "ai" | "manual" | null;
export type MerchantSource = "alias" | "ai" | "manual" | "none";
export type TagKind = "occasion" | "trip" | "other";

export interface ContextTag {
  id: string;
  label: string;
  kind: TagKind;
}

export interface Transaction {
  txn_id: string;
  card: string;
  posted_date: string;
  amount: number;
  raw_description: string;
  normalized_merchant: string;
  canonical_merchant: string | null;
  merchant_source: MerchantSource;
  proposed_canonical: string | null;
  source_file: string;
  category: string | null;
  subcategory: string | null;
  tags: string[];
  classified_by: ClassifiedBy;
  proposed_category: string | null;
  proposed_subcategory: string | null;
}

export interface Counts {
  rule: number;
  merchant: number;
  ai: number;
  manual: number;
  open: number;
  total: number;
}

export interface Status {
  ledger_exists: boolean;
  counts: Partial<Counts>;
  canonical_merchants: number;
  unknown_merchants: number;
  review_pending: number;
  cardholders: string[];
  inbox_files: { card: string; name: string }[];
  duckdb: boolean;
  exports: boolean;
  ollama_available: boolean;
}

export interface RecurringRow {
  normalized_merchant: string;
  occurrences: number;
  avg_amount: number;
  std_amount: number;
  median_gap_days: number | null;
  is_recurring: boolean;
  category: string | null;
  subcategory: string | null;
}

export interface ReconciliationRow {
  bill: string;
  status: "matched" | "missing" | "amount_mismatch";
  expected_amount: number | null;
  matched_merchant: string | null;
  matched_avg: number | null;
  last_seen: string | null;
}

export interface CategoryMonthly {
  month: string;
  category: string;
  total: number;
  txn_count: number;
}

export interface OverviewMonth {
  month: string | null;
  months: string[];
  cardholder: string | null;
  spend_total: number;
  prior_spend_total: number | null;
  spend_delta: number | null;
  spend_delta_pct: number | null;
  charge_count: number;
  payments_and_refunds: number;
  uncategorized_total: number;
  uncategorized_count: number;
  review_count: number;
  categories: { category: string; total: number; prior_total: number | null; delta: number | null }[];
  holders: { name: string; total: number }[];
  large_charges: {
    posted_date: string | null;
    merchant: string;
    amount: number;
    category: string | null;
    cardholder: string | null;
  }[];
  tagged: { id: string; label: string; kind: string; total: number }[];
  bills: { bill: string; status: "seen" | "missing" }[];
}

export interface CardStatement {
  id: string;
  file_name: string;
  txn_count: number;
  spend_total: number;
  payments_and_refunds: number;
  coverage_start: string | null;
  coverage_end: string | null;
}

export interface CardGap {
  after: string;
  before: string;
  days: number;
}

export interface CardProductCoverage {
  issuer: string;
  product: string;
  cardholder: string | null;
  label: string;
  status: "ok" | "gap" | "stale" | "none";
  statement_count: number;
  charge_count: number;
  spend_total: number;
  payments_and_refunds: number;
  uncategorized_count: number;
  uncategorized_total: number;
  first_posted: string | null;
  last_posted: string | null;
  coverage_start: string | null;
  coverage_end: string | null;
  stale_days: number | null;
  statements: CardStatement[];
  gaps: CardGap[];
}

export interface CardsCoverage {
  products: CardProductCoverage[];
  selected: { issuer: string; product: string; cardholder: string | null } | null;
}

export interface Alias {
  regex?: string;
  exact?: string;
}

export interface Merchant {
  canonical: string;
  category: string | null;
  subcategory: string | null;
  aliases: Alias[];
  txn_count: number;
  total_amount: number;
}

export interface UnknownCluster {
  cluster_id: string;
  members: string[];
  representative: string;
  sample_raw: string;
  txn_count: number;
  total_amount: number;
  proposed_canonical: string | null;
}

export interface Rule {
  index: number;
  match: { merchant_regex?: string; merchant_canonical?: string; merchant_exact?: string };
  category: string;
  subcategory: string;
}

export interface Job {
  id: string;
  kind: string;
  status: "pending" | "running" | "done" | "error";
  result: unknown;
  error: string | null;
}

/** Returned when a stage is kicked off; poll /api/jobs/{job_id} for the result. */
export interface JobStart {
  job_id: string;
  kind: string;
  status: string;
}

export interface ReviewQueue {
  total: number;
  items: Transaction[];
  categories: string[];
  subcategories: Record<string, string[]>;
}

export interface AiStatus {
  host: string;
  model: string;
  available: boolean;
  model_installed: boolean;
  gpu_resident: boolean;
  size_vram: number;
  message: string;
}

export interface AiProposal {
  proposal_id: string;
  kind: "merchant" | "category";
  status: "pending" | "deferred" | "applied" | "rejected";
  members: string[];
  txn_ids: string[];
  recommendation: {
    canonical?: string;
    category?: string;
    subcategory?: string;
    reusable?: boolean;
  };
  evidence: {
    reason?: string;
    ambiguous?: boolean;
    sample_raw?: string[];
    txn_count?: number;
    total_amount?: number;
    history_category?: string | null;
    history_ratio?: number;
  };
  confidence: "high" | "medium" | "low";
  model: string;
  created_at: string;
}
