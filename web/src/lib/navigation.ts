/**
 * Redirect-target hardening.
 *
 * `?redirect=` is attacker-controllable, so only same-origin absolute paths are
 * accepted. Backslashes are rejected as well — browsers normalise `\` to `/`,
 * which is the classic open-redirect bypass (and the reason for the recent
 * react-router advisory).
 */
export function safeRedirectPath(raw: string | null | undefined, fallback = "/"): string {
  if (!raw) return fallback;
  if (!raw.startsWith("/")) return fallback;
  if (raw.startsWith("//")) return fallback;
  if (raw.includes("\\")) return fallback;
  if (raw.includes("\n") || raw.includes("\r")) return fallback;
  return raw;
}
