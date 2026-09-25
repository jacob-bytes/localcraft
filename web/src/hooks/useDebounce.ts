import * as React from "react";

/**
 * Debounced value (docs/04 §6.3, §9 — search input waits 300ms before it is
 * written to the URL and therefore before it triggers a request).
 */
export function useDebounce<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);

  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);

  return debounced;
}
