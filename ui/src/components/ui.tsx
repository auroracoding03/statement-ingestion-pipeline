import type { ReactNode } from "react";

export function PageHeader({ title, lede }: { title: string; lede?: string }) {
  return (
    <section className="hero">
      <h1>{title}</h1>
      {lede && <p className="lede">{lede}</p>}
    </section>
  );
}

export function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <article className="metric">
      <span className="label">{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

export function Loading({ what = "data" }: { what?: string }) {
  return <p className="muted">Loading {what}…</p>;
}

export function ErrorNote({ error }: { error: string }) {
  return <p className="pipeline-msg bad">{error}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted empty">{children}</p>;
}

export function MerchantCell({
  canonical,
  normalized,
}: {
  canonical: string | null;
  normalized: string;
}) {
  if (canonical) {
    return (
      <span className="merchant">
        <strong>{canonical}</strong>
        <span className="merchant-raw">{normalized}</span>
      </span>
    );
  }
  return (
    <span className="merchant">
      <span className="unresolved">{normalized}</span>
      <span className="merchant-raw">no canonical match</span>
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status.replace(/_/g, " ")}</span>;
}
