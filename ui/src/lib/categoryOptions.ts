import { sortedLabels } from "./sort";

export type CategoryVocab = {
  categories?: string[];
  subcategories?: Record<string, string[]>;
};

function addLabels(target: Set<string>, values: Iterable<string> | undefined) {
  for (const value of values ?? []) {
    const trimmed = value.trim();
    if (trimmed) target.add(trimmed);
  }
}

/** Rules vocabulary plus any labels seen on spend, sorted A–Z. */
export function categoryLabels(
  spendCategories: Iterable<string>,
  vocab?: CategoryVocab | null,
  extra: Iterable<string> = [],
): string[] {
  const labels = new Set<string>();
  addLabels(labels, spendCategories);
  addLabels(labels, vocab?.categories);
  addLabels(labels, extra);
  return sortedLabels(labels);
}

/** Rules subcategories for one category plus any seen on spend, sorted A–Z. */
export function subcategoryLabels(
  category: string,
  spendSubcategories: Iterable<string>,
  vocab?: CategoryVocab | null,
): string[] {
  if (!category.trim()) return [];
  const labels = new Set<string>();
  addLabels(labels, spendSubcategories);
  addLabels(labels, vocab?.subcategories?.[category]);
  return sortedLabels(labels);
}
