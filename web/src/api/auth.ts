import { acceptTokenPair, clearSession, request, refreshSession } from "./client";
import type {
  AuthProviderResponse,
  ChangePasswordRequest,
  LoginRequest,
  StatusResponse,
  TokenPair,
  User,
} from "./types";

/**
 * Auth endpoints (CONTRACT §3, docs/03 §3.2).
 *
 * CONTRACT §14.2: logout and change-password answer `200` + `{"status":"ok"}` —
 * the typed return value reflects that, and `request<T>` keeps the body instead
 * of assuming `204`.
 */

/** POST /auth/login — stores the pair in memory and returns it (CONTRACT §3.2 ①). */
export async function login(payload: LoginRequest): Promise<TokenPair> {
  const pair = await request<TokenPair>("/auth/login", {
    method: "POST",
    body: payload,
    auth: false,
    skipRefresh: true,
  });
  acceptTokenPair(pair);
  return pair;
}

/**
 * POST /auth/logout — revokes the refresh token server-side, then clears memory.
 * Failures are swallowed on purpose: the local session must be dropped either
 * way, otherwise the user is stuck in a half-logged-in state (CONTRACT §3.2 ⑤).
 */
export async function logout(): Promise<StatusResponse | null> {
  try {
    return await request<StatusResponse>("/auth/logout", { method: "POST" });
  } catch {
    /* best effort — local state is cleared in `finally` */
    return null;
  } finally {
    clearSession();
  }
}

/** GET /auth/me — only needed where the response has drifted from memory. */
export function fetchMe(signal?: AbortSignal): Promise<User> {
  return request<User>("/auth/me", { signal });
}

/** POST /auth/change-password (CONTRACT §14.2: `200` + `{"status":"ok"}`). */
export function changePassword(payload: ChangePasswordRequest): Promise<StatusResponse> {
  return request<StatusResponse>("/auth/change-password", { method: "POST", body: payload });
}

/** GET /auth/provider — public, drives the pluggable login form (FR-AUTH-09). */
export function fetchAuthProvider(signal?: AbortSignal): Promise<AuthProviderResponse> {
  return request<AuthProviderResponse>("/auth/provider", { auth: false, signal });
}

export const authProviderQueryKey = ["auth", "provider"] as const;

export { refreshSession };
