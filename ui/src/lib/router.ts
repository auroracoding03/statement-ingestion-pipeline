import { useEffect, useState } from "react";

export type HashParams = Record<string, string>;

function parseHash(): { path: string; params: HashParams } {
  const raw = window.location.hash.replace(/^#/, "");
  const [pathPart, queryPart = ""] = raw.split("?");
  const path = pathPart && pathPart.startsWith("/") ? pathPart : "/";
  const params: HashParams = {};
  const search = new URLSearchParams(queryPart);
  search.forEach((value, key) => {
    if (value) params[key] = value;
  });
  return { path, params };
}

export function useHashPath() {
  return useHashLocation().path;
}

export function useHashLocation() {
  const [location, setLocation] = useState(parseHash);

  useEffect(() => {
    const update = () => setLocation(parseHash());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  return location;
}

export function hashHref(
  path: string,
  params?: Record<string, string | number | boolean | null | undefined>,
) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === "" || value === undefined || value === null || value === false) continue;
    qs.set(key, String(value));
  }
  qs.sort();
  const query = qs.toString();
  return query ? `#${path}?${query}` : `#${path}`;
}

/** Update the hash without adding a history entry. */
export function replaceHash(
  path: string,
  params?: Record<string, string | number | boolean | null | undefined>,
) {
  const next = hashHref(path, params);
  if (window.location.hash === next) return;
  history.replaceState(null, "", next);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}
