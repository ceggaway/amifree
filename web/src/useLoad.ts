import { useCallback, useEffect, useState } from "react";

/** Load data on mount, with a reload function for after a change. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = useCallback(() => {
    load()
      .then((value) => {
        setData(value);
        setError("");
      })
      .catch((e: Error) => setError(e.message || "Couldn't load. Pull down to retry."));
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, reload };
}
