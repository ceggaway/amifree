import { useCallback, useEffect, useRef, useState } from "react";

/** Load data on mount, with a reload function for after a change. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  // Only the latest request may set the result: a slow earlier answer (say, the previous
  // period on the stats page) must not replace a newer one.
  const latest = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = useCallback(() => {
    const request = ++latest.current;
    load()
      .then((value) => {
        if (request !== latest.current) return;
        setData(value);
        setError("");
      })
      .catch((e: Error) => {
        if (request === latest.current) setError(e.message || "Couldn't load. Pull down to retry.");
      });
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, reload };
}
