import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import * as authApi from "@/api/auth";
import {
  clearSession,
  setPasswordChangeRequiredHandler,
  setSessionExpiredHandler,
  setSessionRefreshedHandler,
} from "@/api/client";
import type { Role, User } from "@/api/types";
import { hasAnyRole } from "@/lib/permissions";

/**
 * Auth/session context. Holds exactly two pieces of global state: the current
 * user and whether the session is known yet. The access token itself never
 * leaves `api/client.ts` (CONTRACT §3.1).
 *
 * `status === "unknown"` is the boot state: the refresh call that restores the
 * session has not answered yet. Route guards MUST render a loading state while
 * unknown, otherwise every F5 flashes the login page (acceptance #4).
 */

export type AuthStatus = "unknown" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  status: AuthStatus;
  user: User | null;
  /** Returns the logged-in user. Throws `ApiError` on failure. */
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  /** Silent refresh; returns `null` when there is no valid session. */
  refresh: () => Promise<User | null>;
  /** Drops memory + cached server state without calling the API. */
  clearLocalSession: () => void;
  hasRole: (roles: Role[]) => boolean;
}

const AuthContext = React.createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<AuthStatus>("unknown");
  const [user, setUser] = React.useState<User | null>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const navigateRef = React.useRef(navigate);
  // Keep the latest navigate available to the 401 handler without touching a ref
  // during render (and without re-registering the handler on every navigation).
  React.useEffect(() => {
    navigateRef.current = navigate;
  }, [navigate]);

  /* ---- boot: restore the session from the refresh cookie (CONTRACT §3.2 ②) ---- */
  React.useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const pair = await authApi.refreshSession();
        if (!active) return;
        setUser(pair.user);
        setStatus("authenticated");
      } catch {
        if (!active) return;
        setUser(null);
        setStatus("unauthenticated");
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const clearLocalSession = React.useCallback(() => {
    clearSession();
    setUser(null);
    setStatus("unauthenticated");
    queryClient.clear();
  }, [queryClient]);

  /* ---- wire client-level 401 / 403 events to app behaviour ---- */
  React.useEffect(() => {
    setSessionExpiredHandler(() => {
      clearSession();
      setUser(null);
      setStatus("unauthenticated");
      queryClient.clear();
      const { pathname, search } = window.location;
      if (pathname === "/login") return;
      const redirect = encodeURIComponent(`${pathname}${search}`);
      navigateRef.current(`/login?redirect=${redirect}`, { replace: true });
    });

    setPasswordChangeRequiredHandler(() => {
      if (window.location.pathname !== "/change-password") {
        navigateRef.current("/change-password", { replace: true });
      }
    });

    setSessionRefreshedHandler((pair) => {
      setUser(pair.user);
      setStatus("authenticated");
    });

    return () => {
      setSessionExpiredHandler(null);
      setPasswordChangeRequiredHandler(null);
      setSessionRefreshedHandler(null);
    };
  }, [queryClient]);

  const login = React.useCallback(async (username: string, password: string) => {
    const pair = await authApi.login({ username, password });
    setUser(pair.user);
    setStatus("authenticated");
    return pair.user;
  }, []);

  const logout = React.useCallback(async () => {
    await authApi.logout();
    setUser(null);
    setStatus("unauthenticated");
    queryClient.clear();
  }, [queryClient]);

  const refresh = React.useCallback(async () => {
    try {
      const pair = await authApi.refreshSession();
      setUser(pair.user);
      setStatus("authenticated");
      return pair.user;
    } catch {
      clearSession();
      setUser(null);
      setStatus("unauthenticated");
      return null;
    }
  }, []);

  const hasRole = React.useCallback(
    (roles: Role[]) => hasAnyRole(user, roles),
    [user],
  );

  const value = React.useMemo<AuthContextValue>(
    () => ({ status, user, login, logout, refresh, clearLocalSession, hasRole }),
    [status, user, login, logout, refresh, clearLocalSession, hasRole],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = React.useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within <AuthProvider>");
  }
  return context;
}
