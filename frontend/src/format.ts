/** Small shared formatting helpers for the profile and admin views. */

/**
 * Format an API timestamp.
 *
 * The database stores UTC, but a naive timestamp (SQLite returns no offset) would
 * otherwise be read as local time and shown hours off. A missing timezone
 * designator is therefore treated as UTC.
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  const date = new Date(hasZone ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "3 hours ago" style relative time, for scanning a queue quickly. */
export function relativeTime(value: string | null | undefined): string {
  if (!value) return "";
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  const then = new Date(hasZone ? value : `${value}Z`).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 31536000],
    ["month", 2592000],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of units) {
    if (seconds >= size) return formatter.format(-Math.round(seconds / size), unit);
  }
  return "just now";
}
