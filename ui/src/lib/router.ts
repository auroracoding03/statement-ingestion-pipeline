import { useEffect, useState } from "react";

function currentPath() {
  const hash = window.location.hash.replace(/^#/, "");
  return hash && hash.startsWith("/") ? hash : "/";
}

export function useHashPath() {
  const [path, setPath] = useState(currentPath);

  useEffect(() => {
    const update = () => setPath(currentPath());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  return path;
}

export function hashHref(path: string) {
  return `#${path}`;
}
