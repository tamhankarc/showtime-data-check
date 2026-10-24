const routes = [
  /^(session|login|logout|bootstrap)$/,
  /^catalogs\/refresh$/,
  /^jobs\/[a-zA-Z0-9_-]+$/,
  /^checks(?:\/[a-zA-Z0-9_-]+(?:\/(?:retry|export))?)?$/,
  /^(countries|connections|theaters|provider-theaters|provider-movies|format-mappings|movie-format-overrides|movies|movie-groups)(?:\/[a-zA-Z0-9_-]+)?$/,
  /^movie-groups\/move$/,
];
export function allowedPath(path: string[]): boolean {
  return (
    path.length > 0 &&
    path.every(
      (part) => ![".", ".."].includes(part) && !/[\\%?#]/.test(part),
    ) &&
    routes.some((route) => route.test(path.join("/")))
  );
}
