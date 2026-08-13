export type PeriodPreset = "month" | "prev_month" | "t3m" | "t12m" | "ytd" | "custom";

export const PERIOD_PRESETS: { id: PeriodPreset; label: string }[] = [
  { id: "month", label: "Latest month" },
  { id: "prev_month", label: "Previous" },
  { id: "t3m", label: "Trailing 3M" },
  { id: "t12m", label: "Trailing 12M" },
  { id: "ytd", label: "YTD" },
  { id: "custom", label: "Custom" },
];

export function shiftMonth(month: string, delta: number): string {
  const [year, monthN] = month.split("-").map(Number);
  const total = year * 12 + (monthN - 1) + delta;
  const nextYear = Math.floor(total / 12);
  const nextMonth = (total % 12) + 1;
  return `${String(nextYear).padStart(4, "0")}-${String(nextMonth).padStart(2, "0")}`;
}

export function lastDayOfMonth(month: string): string {
  const [year, monthN] = month.split("-").map(Number);
  const day = new Date(year, monthN, 0).getDate();
  return `${month}-${String(day).padStart(2, "0")}`;
}

export function monthsInRange(months: string[], since: string, until: string): string[] {
  const start = since.slice(0, 7);
  const end = until.slice(0, 7);
  return months.filter((month) => month >= start && month <= end);
}

export function resolveClientPeriod(opts: {
  preset: PeriodPreset;
  month: string;
  since: string;
  until: string;
  months: string[];
}): { since: string; until: string; month: string } {
  const asOf = opts.month || opts.months[opts.months.length - 1] || "";
  if (opts.preset === "custom") {
    return { since: opts.since, until: opts.until, month: asOf };
  }
  if (!asOf) return { since: opts.since, until: opts.until, month: "" };
  if (opts.preset === "month") {
    return { since: `${asOf}-01`, until: lastDayOfMonth(asOf), month: asOf };
  }
  if (opts.preset === "prev_month") {
    const prev = shiftMonth(asOf, -1);
    return { since: `${prev}-01`, until: lastDayOfMonth(prev), month: asOf };
  }
  if (opts.preset === "t3m") {
    const start = shiftMonth(asOf, -2);
    return { since: `${start}-01`, until: lastDayOfMonth(asOf), month: asOf };
  }
  if (opts.preset === "t12m") {
    const start = shiftMonth(asOf, -11);
    return { since: `${start}-01`, until: lastDayOfMonth(asOf), month: asOf };
  }
  const start = `${asOf.slice(0, 4)}-01`;
  return { since: `${start}-01`, until: lastDayOfMonth(asOf), month: asOf };
}
