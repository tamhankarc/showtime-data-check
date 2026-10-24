export interface Choice {
  value: string;
  label: string;
  detail?: string;
  search?: string;
}
export function normalize(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLocaleLowerCase()
    .trim();
}
export function rankChoices(choices: Choice[], query: string): Choice[] {
  const q = normalize(query);
  const score = (choice: Choice): number => {
    const label = normalize(choice.label);
    const words = label.split(/[^\p{L}\p{N}]+/u);
    if (!q) return 0;
    if (label === q) return 0;
    if (label.startsWith(q)) return 1;
    if (words.some((word) => word.startsWith(q))) return 2;
    if (label.includes(q)) return 3;
    if (normalize(`${choice.detail || ""} ${choice.search || ""}`).includes(q))
      return 4;
    return 99;
  };
  return choices
    .filter((choice) => score(choice) < 99)
    .sort(
      (a, b) =>
        score(a) - score(b) ||
        a.label.localeCompare(b.label, undefined, {
          sensitivity: "base",
          numeric: true,
        }),
    );
}
export function genericInitial(name: string): string {
  return (
    normalize(name)
      .match(/[a-z0-9]/)?.[0]
      .toUpperCase() || "T"
  );
}
export function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export interface ProviderMovieIdentity {
  provider_code?: unknown;
  external_id?: unknown;
  url_key?: unknown;
  name?: unknown;
}
export function providerMovieIdentifier(movie: ProviderMovieIdentity): string {
  if (String(movie.provider_code || "").toUpperCase() === "INGRESSO") {
    return typeof movie.url_key === "string" && movie.url_key.trim()
      ? movie.url_key.trim()
      : "URL key not yet available";
  }
  return String(movie.external_id ?? "").trim() || "—";
}
export function providerMovieLabel(movie: ProviderMovieIdentity): string {
  return `${String(movie.name || "Unnamed movie")} (${providerMovieIdentifier(movie)})`;
}
export function providerMovieChoice(movie: {
  id: number;
  name: string;
  external_id: string;
  provider_code: string;
  url_key?: string | null;
  original_name?: string;
  group_name?: string;
}): Choice {
  return {
    value: String(movie.id),
    label: providerMovieLabel(movie),
    detail: `${movie.provider_code}${movie.original_name && movie.original_name !== movie.name ? ` · ${movie.original_name}` : ""}${movie.group_name && movie.group_name !== movie.name ? ` · Group: ${movie.group_name}` : ""}`,
    search: `${providerMovieIdentifier(movie)} ${movie.original_name || ""} ${movie.group_name || ""}`,
  };
}
export function recordSearchValues(value: unknown): string[] {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) return value.flatMap(recordSearchValues);
  if (typeof value === "object")
    return Object.values(value).flatMap(recordSearchValues);
  return [String(value)];
}
