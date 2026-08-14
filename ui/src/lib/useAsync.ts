import { useCallback, useEffect, useRef, useState } from "react";

interface ReloadOptions {
  silent?: boolean;
}

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: (options?: ReloadOptions) => void;
}

/** Fetch-on-mount with a manual reload handle, cancelling stale responses. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const silentRef = useRef(false);

  const reload = useCallback((options?: ReloadOptions) => {
    silentRef.current = Boolean(options?.silent);
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    let active = true;
    const silent = silentRef.current;
    silentRef.current = false;
    if (!silent) setLoading(true);
    setError(null);
    fn()
      .then((result) => {
        if (active) setData(result);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, loading, error, reload };
}
