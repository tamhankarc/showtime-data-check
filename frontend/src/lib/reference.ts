interface ReferenceFilters {
  resource: string;
  q: string;
  exact: boolean;
  page: number;
  country: string;
  provider: string;
  status: string;
  showInactiveTheaters: boolean;
  includeArchivedMovies?: boolean;
}

export function referenceListQuery(filters: ReferenceFilters): URLSearchParams {
  const params = new URLSearchParams({
    q: filters.q,
    exact: String(filters.exact),
    page: String(filters.page),
    page_size: "50",
  });
  if (filters.country) params.set("country", filters.country);
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.status) params.set("status", filters.status);
  if (filters.resource === "theaters" && filters.showInactiveTheaters)
    params.set("include_inactive", "true");
  if (filters.resource === "movie-groups" && filters.includeArchivedMovies)
    params.set("include_inactive", "true");
  return params;
}
