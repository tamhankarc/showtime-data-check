import type { Role } from "./types";

export const pagePaths = {
  home: "/",
  catalog: "/provider-movies/",
  showtimes: "/showtimes/",
  theaters: "/theaters-by-movie/",
  countries: "/countries/",
  movies: "/movies/",
  directory: "/theaters/",
  assignments: "/provider-theaters/",
  formats: "/formats/",
} as const;
export type Page = keyof typeof pagePaths;
export const regularPages: Page[] = [
  "home",
  "catalog",
  "showtimes",
  "theaters",
];
export const adminPages: Page[] = [
  "countries",
  "movies",
  "directory",
  "assignments",
  "formats",
];
export const sectionSlugs = Object.values(pagePaths)
  .filter((path) => path !== "/")
  .map((path) => path.slice(1, -1));

export function sectionForPath(pathname: string | null): Page | null {
  if (!pathname || pathname.includes("?") || pathname.includes("#"))
    return null;
  const canonical =
    pathname === "/" || pathname.endsWith("/") ? pathname : `${pathname}/`;
  const found = Object.entries(pagePaths).find(
    ([, path]) => path === canonical,
  );
  return found ? (found[0] as Page) : null;
}
export function sectionAllowed(role: Role, section: Page | null): boolean {
  return (
    section !== null && (role === "admin" || regularPages.includes(section))
  );
}
export function apiUrl(path: string): string {
  const separator = path.indexOf("?");
  const pathname = (separator < 0 ? path : path.slice(0, separator)).replace(
    /^\/+|\/+$/g,
    "",
  );
  return `/api/${pathname}/${separator < 0 ? "" : path.slice(separator)}`;
}
