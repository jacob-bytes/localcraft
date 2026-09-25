import { acceptTokenPair, clearSession, request, refreshSession } from "./client";
import type { ChangePasswordRequest, LoginRequest, TokenPair, User } from "./types";

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
export async function logout(): Promise<void> {
  try {
    await request<null>("/auth/logout", { method: "POST" });
  } catch {
    /* best effort — local state is cleared in `finally` */
  } finally {
    clearSession();
  }
}

/** GET /auth/me — only needed where the response has drifted from memory. */
export function fetchMe(signal?: AbortSignal): Promise<User> {
  return request<User>("/auth/me", { signal });
}

/** POST /auth/change-password */
export function changePassword(payload: ChangePasswordRequest): Promise<null> {
  return request<null>("/auth/change-password", { method: "POST", body: payload });
}

export { refreshSession };
