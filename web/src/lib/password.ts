/**
 * Password strength, per docs/04 §6.2 and CONTRACT §11:
 * at least 10 characters AND at least 3 of the 4 character classes
 * (lowercase / uppercase / digit / symbol). The server re-checks the same rule —
 * this is UX only.
 */
export interface PasswordStrength {
  score: 0 | 1 | 2 | 3 | 4;
  label: string;
  classes: number;
  meetsPolicy: boolean;
}

export function evaluatePassword(password: string): PasswordStrength {
  if (!password) return { score: 0, label: "", classes: 0, meetsPolicy: false };

  const classes =
    (/[a-z]/.test(password) ? 1 : 0) +
    (/[A-Z]/.test(password) ? 1 : 0) +
    (/[0-9]/.test(password) ? 1 : 0) +
    (/[^A-Za-z0-9]/.test(password) ? 1 : 0);

  const meetsPolicy = password.length >= 10 && classes >= 3;

  let score: PasswordStrength["score"] = 1;
  if (password.length >= 8) score = 2;
  if (meetsPolicy) score = 3;
  if (password.length >= 14 && classes >= 4) score = 4;

  const labels: Record<PasswordStrength["score"], string> = {
    0: "",
    1: "弱",
    2: "一般",
    3: "强",
    4: "很强",
  };

  return { score, label: labels[score], classes, meetsPolicy };
}
