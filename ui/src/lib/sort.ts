/** Case-insensitive A–Z order for dropdown labels. */
export function compareLabel(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: "base" });
}

export function sortedLabels(values: Iterable<string>): string[] {
  return [...values].sort(compareLabel);
}
