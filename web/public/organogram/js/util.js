export function parseDate(s) {
  if (!s) return null;
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : t;
}

export function toISODate(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

const DAY_MS = 86_400_000;

export function dateRange() {
  const max = Date.now() + 30 * DAY_MS;
  const min = Date.parse("1900-01-01");
  return { min, max, step: DAY_MS };
}

export function isActiveOn(record, dateMs) {
  if (dateMs == null) return true;
  const from = parseDate(record.valid_from ?? record.start_date) ?? -Infinity;
  const until = parseDate(record.valid_until ?? record.end_date) ?? Infinity;
  return from <= dateMs && dateMs <= until;
}
