import { NavLink, Outlet } from "react-router-dom";

import { canWrite } from "../lib/dataSource";
import { PipelineBar } from "./PipelineBar";

const LINKS = [
  { to: "/", label: "Overview", end: true },
  { to: "/transactions", label: "Transactions" },
  { to: "/categories", label: "Categories" },
  { to: "/recurring", label: "Recurring" },
  { to: "/merchants", label: "Merchants" },
  { to: "/review", label: "Review", writeOnly: true },
  { to: "/rules", label: "Rules", writeOnly: true },
];

export function Layout() {
  return (
    <div className="app">
      <header className="site-header">
        <div className="brand">
          Finance Ledger
          {!canWrite && <span className="badge">read-only</span>}
        </div>
        <nav>
          {LINKS.filter((l) => canWrite || !l.writeOnly).map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end}>
              {link.label}
            </NavLink>
          ))}
        </nav>
      </header>

      {canWrite && <PipelineBar />}

      <main>
        <Outlet />
      </main>
    </div>
  );
}
