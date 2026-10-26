export function providerMoviesEndpoint(
  country: string,
  provider: string,
  includeArchived = false,
): string {
  const params = new URLSearchParams({ country, provider });
  if (includeArchived) params.set("include_inactive", "true");
  return `provider-movies?${params}`;
}

export function catalogWindowValue(value: unknown): number | null {
  if (value == null || (typeof value === "string" && !value.trim()))
    return null;
  if (typeof value !== "number" && typeof value !== "string")
    throw new Error(
      "Enter a whole number from 1 to 3650, or leave the catalog window blank for unlimited.",
    );
  const days = Number(value);
  if (days === 0) return null;
  if (!Number.isSafeInteger(days) || days < 1 || days > 3650)
    throw new Error(
      "Enter a whole number from 1 to 3650, or leave the catalog window blank for unlimited.",
    );
  return days;
}
