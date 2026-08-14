/** Case-insensitive A–Z order for dropdown labels. */
export function compareLabel(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: "base" });
}

export function sortedLabels(values: Iterable<string>): string[] {
  return [...values].sort(compareLabel);
}

export type SortOrder = "asc" | "desc";

export interface ColumnSort<K extends string> {
  key: K;
  order: SortOrder;
  secondaryKey: K | null;
  secondaryOrder: SortOrder;
}

export function defaultSortOrder(key: string, numericKeys: Iterable<string>): SortOrder {
  return new Set<string>([...numericKeys]).has(key) ? "desc" : "asc";
}

export function nextColumnSort<K extends string>(
  current: ColumnSort<K>,
  clicked: K,
  numericKeys: Iterable<string>,
): ColumnSort<K> {
  if (current.key === clicked) {
    return { ...current, order: current.order === "asc" ? "desc" : "asc" };
  }
  const order =
    current.secondaryKey === clicked ? current.secondaryOrder : defaultSortOrder(clicked, numericKeys);
  return {
    key: clicked,
    order,
    secondaryKey: current.key,
    secondaryOrder: current.order,
  };
}

export function compareText(left: string, right: string, order: SortOrder): number {
  return compareLabel(left, right) * (order === "asc" ? 1 : -1);
}

export function compareNumber(left: number, right: number, order: SortOrder): number {
  if (left === right) return 0;
  return (left < right ? -1 : 1) * (order === "asc" ? 1 : -1);
}

export function compareWithSecondary(primary: number, secondary: number): number {
  return primary !== 0 ? primary : secondary;
}
