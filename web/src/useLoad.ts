import { useCallback, useEffect, useRef, useState } from "react";

// Pages loaded earlier in this session, so Back shows them at once while a fresh copy loads.
// Kept in memory only: it is gone when the Mini App closes.
const cache = new Map<string, unknown>();

/** Drop remembered pages whose key starts with `prefix`, e.g. after a change that makes them stale. */
export function forget(prefix: string) {
  for (const key of [...cache.keys()]) if (key.startsWith(prefix)) cache.delete(key);
}

/** Load data on mount, with a reload function for after a change.
 *
 * With a `key`, the last copy loaded under that key shows straight away and is refreshed
 * in the background, instead of a blank "Loading…" each time the page is opened. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = [], key?: string) {
  const [data, setData] = useState<T | null>(() => (key && cache.has(key) ? (cache.get(key) as T) : null));
  const [error, setError] = useState("");
  // Only the latest request may set the result: a slow earlier answer (say, the previous
  // period on the stats page) must not replace a newer one.
  const latest = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = useCallback(() => {
    const request = ++latest.current;
    // Opening a different item (new deps): show its remembered copy, if any, while it loads.
    if (key && cache.has(key)) setData(cache.get(key) as T);
    load()
      .then((value) => {
        if (request !== latest.current) return;
        if (key) cache.set(key, value);
        setData(value);
        setError("");
      })
      .catch((e: Error) => {
        if (request === latest.current) setError(e.message || "Couldn't load. Pull down to retry.");
      });
  }, deps);
  useEffect(reload, [reload]);

  /** Replace the data after a change made on the page, keeping the remembered copy in step. */
  const update = useCallback(
    (value: T) => {
      latest.current++; // a refresh still in flight is now older than this
      if (key) cache.set(key, value);
      setData(value);
    },
    [key],
  );
  return { data, error, reload, update };
}
