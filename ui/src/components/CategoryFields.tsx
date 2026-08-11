/** Linked category + subcategory selects. Choosing a subcategory sets its parent category. */

const SEP = "::";

export function encodeSubcategory(category: string, subcategory: string): string {
  if (!category || !subcategory) return "";
  return `${category}${SEP}${subcategory}`;
}

export function parseSubcategoryValue(value: string): { category: string; subcategory: string } {
  if (!value.includes(SEP)) return { category: "", subcategory: value };
  const [category, ...rest] = value.split(SEP);
  return { category: category ?? "", subcategory: rest.join(SEP) };
}

export function CategoryFields({
  categories,
  subcategories,
  category,
  subcategory,
  onCategoryChange,
  onPairChange,
  categoryLabel = "Category (optional)",
  subcategoryLabel = "Subcategory (optional)",
  requiredCategory = false,
}: {
  categories: string[];
  subcategories: Record<string, string[]>;
  category: string;
  subcategory: string;
  onCategoryChange: (category: string, subcategory: string) => void;
  onPairChange: (category: string, subcategory: string) => void;
  categoryLabel?: string;
  subcategoryLabel?: string;
  requiredCategory?: boolean;
}) {
  const encoded = category && subcategory ? encodeSubcategory(category, subcategory) : "";
  const primaries = categories.filter((entry) => entry && entry !== "Uncategorized");

  return (
    <>
      <select
        value={category}
        onChange={(e) => {
          const next = e.target.value;
          const allowed = next ? subcategories[next] ?? [] : [];
          const nextSub = subcategory && allowed.includes(subcategory) ? subcategory : "";
          onCategoryChange(next, nextSub);
        }}
        aria-label={categoryLabel}
      >
        <option value="">{requiredCategory ? "Category" : categoryLabel}</option>
        {primaries.map((entry) => (
          <option key={entry} value={entry}>
            {entry}
          </option>
        ))}
      </select>
      <select
        value={encoded}
        onChange={(e) => {
          const raw = e.target.value;
          if (!raw) {
            onPairChange(category, "");
            return;
          }
          const parsed = parseSubcategoryValue(raw);
          onPairChange(parsed.category, parsed.subcategory);
        }}
        aria-label={subcategoryLabel}
      >
        <option value="">{subcategoryLabel}</option>
        {primaries.map((primary) => {
          const options = subcategories[primary] ?? [];
          if (options.length === 0) return null;
          return (
            <optgroup key={primary} label={primary}>
              {options.map((sub) => (
                <option key={`${primary}${SEP}${sub}`} value={encodeSubcategory(primary, sub)}>
                  {sub}
                </option>
              ))}
            </optgroup>
          );
        })}
      </select>
    </>
  );
}
