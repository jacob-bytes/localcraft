import { setupWorker } from "msw/browser";

import { handlers } from "./handlers";

/**
 * Browser-side MSW worker. Imported dynamically from `main.tsx` and only when
 * `VITE_ENABLE_MOCKS=true`; the production build replaces the flag with `false`
 * so this module (and MSW itself) is never bundled (CONTRACT §11 #11).
 */
export const worker = setupWorker(...handlers);

export async function startMocks(): Promise<void> {
  await worker.start({
    serviceWorker: { url: "/mockServiceWorker.js" },
    // Let anything not mocked (e.g. a real backend, static assets) through.
    onUnhandledRequest: "bypass",
    quiet: true,
  });
}
