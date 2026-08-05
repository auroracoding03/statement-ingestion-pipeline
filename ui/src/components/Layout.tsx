import type { ReactNode } from "react";

import { canWrite } from "../lib/dataSource";
import { hashHref, useHashPath } from "../lib/router";
import { PipelineBar } from "./PipelineBar";

const LINKS = [
  { to: "/", label: "Overview" },
  { to: "/transactions", label: "Transactions" },
  { to: "/categories", label: "Categories" },
  { to: "/recurring", label: "Recurring" },
  { to: "/merchants", label: "Merchants" },
  { to: "/review", label: "Review", writeOnly: true },
  { to: "/rules", label: "Rules", writeOnly: true },
];

export function Layout({ children }: { children: ReactNode }) {
  const path = useHashPath();
  return (
    <div className="app">
      <header className="site-header">
        <div className="brand">
          Finance Ledger
          {!canWrite && <span className="badge">read-only</span>}
        </div>
        <nav>
          {LINKS.filter((l) => canWrite || !l.writeOnly).map((link) => (
            <a
              key={link.to}
              href={hashHref(link.to)}
              className={path === link.to ? "active" : undefined}
            >
              {link.label}
            </a>
          ))}
        </nav>
      </header>

      {canWrite && <PipelineBar />}

      <main>
        {children}
      </main>
    </div>
  );
}
