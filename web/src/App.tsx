import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeProvider } from "@/hooks/useTheme";
import { queryClient } from "@/lib/queryClient";
import { router } from "@/routes";

/**
 * Provider stack (docs/04 §1 — no state-management library):
 *   TanStack Query  → server state
 *   ThemeProvider   → theme only (user state lives in AuthProvider, which is
 *                     mounted inside the router so it can navigate on session loss)
 */
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={200}>
          <RouterProvider router={router} />
          <Toaster position="top-center" closeButton />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
