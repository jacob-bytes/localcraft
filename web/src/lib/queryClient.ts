import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";

/**
 * Cache policy (docs/04 §9):
 *  - portal list        staleTime 60s
 *  - `/meta`            staleTime Infinity
 *  - retrying client errors (4xx) is pointless; 5xx and network errors retry twice
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (error instanceof ApiError) {
          if (error.status >= 400 && error.status < 500) return false;
          if (error.status === 0) return failureCount < 2;
        }
        return failureCount < 2;
      },
    },
    mutations: {
      retry: false,
    },
  },
});
