import { useState, type ReactNode } from "react";

import { api, canWrite } from "../lib/dataSource";
import { hashHref, useHashPath } from "../lib/router";

const GROUPS = [
  {
    label: "Ledger",
    links: [
      { to: "/", label: "Overview" },
      { to: "/transactions", label: "Transactions" },
      { to: "/cards", label: "Cards" },
      { to: "/categories", label: "Categories" },
      { to: "/budget", label: "Budget", writeOnly: true },
      { to: "/recurring", label: "Recurring" },
    ],
  },
  {
    label: "Inbox",
    links: [
      { to: "/ingestion", label: "Ingestion", writeOnly: true },
      { to: "/review", label: "Review", writeOnly: true },
      { to: "/ai-assistant", label: "AI proposals", writeOnly: true },
    ],
  },
  {
    label: "Catalog",
    links: [
      { to: "/merchants", label: "Merchants" },
      { to: "/rules", label: "Rules", writeOnly: true },
    ],
  },
  {
    label: "Ask",
    links: [{ to: "/insights", label: "Ask the ledger", writeOnly: true }],
  },
];

export function Layout({ children }: { children: ReactNode }) {
  const path = useHashPath();
  const [updateMessage, setUpdateMessage] = useState("");
  const [checkingUpdate, setCheckingUpdate] = useState(false);

  async function checkForUpdates() {
    setCheckingUpdate(true);
    try {
      const update = await api.updates();
      if (!update.update_available) {
        setUpdateMessage(update.message);
        return;
      }
      const confirmed = window.confirm(
        `Version ${update.latest_version} is ready. Download and install it now? The app will restart.`,
      );
      if (!confirmed) {
        setUpdateMessage(`Version ${update.latest_version} is ready to install.`);
        return;
      }
      const result = await api.installUpdate();
      setUpdateMessage(result.message);
    } catch (error) {
      setUpdateMessage(error instanceof Error ? error.message : "Could not check for updates.");
    } finally {
      setCheckingUpdate(false);
    }
  }

  const groups = GROUPS.map((group) => ({
    ...group,
    links: group.links.filter((link) => canWrite || !link.writeOnly),
  })).filter((group) => group.links.length > 0);

  return (
    <div className="app">
      <header className="site-header">
        <div className="brand">
          Finance Ledger
          {!canWrite && <span className="badge">read-only</span>}
        </div>
        <nav>
          {groups.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="nav-group-label">{group.label}</span>
              <div className="nav-group-links">
                {group.links.map((link) => (
                  <a
                    key={link.to}
                    href={hashHref(link.to)}
                    className={path === link.to ? "active" : undefined}
                    aria-current={path === link.to ? "page" : undefined}
                  >
                    {link.label}
                  </a>
                ))}
              </div>
            </div>
          ))}
        </nav>
        {canWrite && (
          <div className="update-control">
            <button className="btn subtle small" onClick={checkForUpdates} disabled={checkingUpdate}>
              {checkingUpdate ? "Checking…" : "Check for updates"}
            </button>
            {updateMessage && <span className="update-message">{updateMessage}</span>}
          </div>
        )}
      </header>
      <main>
        {children}
      </main>
    </div>
  );
}
