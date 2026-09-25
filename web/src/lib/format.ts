import { format, formatDistanceToNow } from "date-fns";
import { zhCN } from "date-fns/locale";

/**
 * Formatting helpers (docs/04 §2 `lib/format.ts`).
 * The backend always sends UTC ISO-8601 with a `Z`; the browser renders local
 * time (CONTRACT §5).
 */

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "3 天前" — feeds the card footer. */
export function formatRelativeTime(value: string | null | undefined): string {
  const date = parseDate(value);
  if (!date) return "—";
  return formatDistanceToNow(date, { addSuffix: true, locale: zhCN });
}

/** "2025-03-02 14:31" — absolute, used in `title` attributes. */
export function formatDateTime(value: string | null | undefined): string {
  const date = parseDate(value);
  if (!date) return "—";
  return format(date, "yyyy-MM-dd HH:mm");
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex] ?? "B"}`;
}

/** 1234 → "1.2k" — keeps card metadata narrow. */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "0";
  if (value < 1000) return String(value);
  if (value < 10000) return `${(value / 1000).toFixed(1)}k`;
  if (value < 1000000) return `${Math.round(value / 1000)}k`;
  return `${(value / 1000000).toFixed(1)}M`;
}

/** Seconds → "12:34" for the account-lock countdown (docs/04 §6.1). */
export function formatCountdown(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** Avatar fallback: first character of the display name. */
export function initials(name: string | null | undefined): string {
  const trimmed = (name ?? "").trim();
  if (!trimmed) return "?";
  return trimmed.slice(0, 1).toUpperCase();
}
