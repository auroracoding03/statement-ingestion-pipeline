const currency = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
});

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return currency.format(Number(value));
}

export function compactMoney(value: number): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    notation: Math.abs(value) >= 10000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(value) >= 10000 ? 1 : 0,
  }).format(value);
}

export function shortDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function titleCase(value: string): string {
  return value.replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Format integer cents as currency without floating-point multiply surprises. */
export function moneyCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined || Number.isNaN(Number(cents))) return "—";
  const n = Number(cents);
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  const dollars = Math.floor(abs / 100);
  const rem = abs % 100;
  return `${sign}$${dollars.toLocaleString()}.${String(rem).padStart(2, "0")}`;
}

/**
 * Parse a dollar string into integer cents.
 * Accepts whole dollars or one/two decimal places only.
 */
export function dollarsToCents(input: string): number {
  const trimmed = input.trim();
  const match = trimmed.match(/^(\d+)(?:\.(\d{1,2}))?$/);
  if (!match) {
    throw new Error("Enter a valid dollar amount (e.g. 2100 or 2100.50)");
  }
  const dollars = Number(match[1]);
  const centsPart = match[2] ? Number(match[2].padEnd(2, "0")) : 0;
  return dollars * 100 + centsPart;
}

export function centsToDollarInput(cents: number): string {
  const dollars = Math.floor(cents / 100);
  const rem = cents % 100;
  return rem === 0 ? String(dollars) : `${dollars}.${String(rem).padStart(2, "0")}`;
}

export function currentMonthLocal(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${y}-${m}`;
}

export function todayLocalISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
