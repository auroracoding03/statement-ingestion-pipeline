import { useEffect, useState, type ReactNode } from "react";
import { Settings } from "lucide-react";

import { api, canWrite } from "../lib/dataSource";
import { hashHref, useHashPath } from "../lib/router";
import { useAsync } from "../lib/useAsync";

const GROUPS = [
  {
    label: "Ledger",
    links: [
      { to: "/", label: "Overview" },
      { to: "/transactions", label: "Transactions" },
      { to: "/accounts", label: "Accounts" },
      { to: "/categories", label: "Spend" },
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

function navIsActive(to: string, path: string): boolean {
  if (to === "/accounts") return path === "/accounts" || path === "/cards";
  return path === to;
}

export function Layout({ children }: { children: ReactNode }) {
  const path = useHashPath();
  const [settingsOpen, setSettingsOpen] = useState(false);

  const groups = GROUPS.map((group) => ({
    ...group,
    links: group.links.filter((link) => canWrite || !link.writeOnly),
  })).filter((group) => group.links.length > 0);

  return (
    <div className="app">
      <header className="site-header">
        <div className="site-header-top">
          <div className="brand">
            Family Finance
            {!canWrite && <span className="badge">read-only</span>}
          </div>
          {canWrite && (
            <div className="update-control">
              <button
                className="btn icon-button"
                type="button"
                aria-label="Settings"
                onClick={() => setSettingsOpen(true)}
              >
                <Settings size={22} strokeWidth={2} absoluteStrokeWidth aria-hidden="true" />
              </button>
            </div>
          )}
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
                    className={navIsActive(link.to, path) ? "active" : undefined}
                    aria-current={navIsActive(link.to, path) ? "page" : undefined}
                  >
                    {link.label}
                  </a>
                ))}
              </div>
            </div>
          ))}
        </nav>
      </header>
      <main>{children}</main>
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}

function formatUploadStamp(value: string | null | undefined): string {
  if (!value) return "No statements uploaded yet";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function SettingsModal({ onClose }: { onClose: () => void }) {
  const status = useAsync(() => api.status(), []);
  const [armed, setArmed] = useState(false);
  const [updateMessage, setUpdateMessage] = useState("");
  const [checkingUpdate, setCheckingUpdate] = useState(false);

  useEffect(() => {
    const id = window.setTimeout(() => setArmed(true), 0);
    return () => window.clearTimeout(id);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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

  return (
    <div
      className={`modal-backdrop${armed ? "" : " is-pending"}`}
      onClick={(event) => {
        if (!armed || event.target !== event.currentTarget) return;
        onClose();
      }}
      role="presentation"
    >
      <div
        className="upload-modal settings-modal"
        role="dialog"
        aria-labelledby="settings-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="settings-title">Settings</h2>
        <dl className="review-fields">
          <dt>Last statement upload</dt>
          <dd>{status.loading ? "Loading…" : formatUploadStamp(status.data?.last_statement_upload_at)}</dd>
          <dt>Application version</dt>
          <dd>{status.loading ? "Loading…" : status.data?.version || "Unknown"}</dd>
        </dl>
        {status.error && <p className="pipeline-msg bad">{status.error}</p>}
        {updateMessage && <p className="update-message">{updateMessage}</p>}
        <div className="review-actions">
          <button className="btn" type="button" onClick={() => void checkForUpdates()} disabled={checkingUpdate}>
            {checkingUpdate ? "Checking…" : "Check for updates"}
          </button>
          <button className="btn subtle" type="button" onClick={onClose} disabled={checkingUpdate}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
