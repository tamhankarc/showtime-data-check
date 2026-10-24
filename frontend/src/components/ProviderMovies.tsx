"use client";
import { useEffect, useState } from "react";
import { api, items } from "@/lib/api";
import type { Bootstrap, ListPage, ProviderMovieRecord } from "@/lib/types";
import { Combobox } from "./Combobox";
import { Empty, ErrorNotice, Pagination, Status } from "./Shared";
export function ProviderMovies({ data }: { data: Bootstrap }) {
  const [country, setCountry] = useState("");
  const [provider, setProvider] = useState("all");
  const [search, setSearch] = useState("");
  const [exact, setExact] = useState(false);
  const [page, setPage] = useState(1);
  const [movies, setMovies] = useState<ProviderMovieRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!country) {
      setMovies([]);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const timer = setTimeout(
      () =>
        api<ListPage<ProviderMovieRecord>>(
          `provider-movies?country=${encodeURIComponent(country)}&provider=${encodeURIComponent(provider)}&q=${encodeURIComponent(search)}&exact=${exact}&page=${page}&page_size=50`,
          { signal: controller.signal },
        )
          .then((value) => {
            setMovies(items(value));
            setTotal(value.total);
          })
          .catch((e) => {
            if (e.name !== "AbortError") setError(e.message);
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          }),
      search ? 250 : 0,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [country, provider, search, exact, page]);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Imported catalog</p>
          <h1>Provider movies</h1>
          <p>
            All available provider movies and their original IDs. Refresh their
            catalog from Home.
          </p>
        </div>
      </div>
      <section className="panel">
        <div className="fields">
          <Combobox
            label="Country"
            value={country}
            placeholder="Choose a country"
            options={data.countries.map((c) => ({
              value: c.code,
              label: `${c.name} (${c.code})`,
            }))}
            onChange={(v) => {
              setCountry(v[0] || "");
              setProvider("all");
              setPage(1);
            }}
          />
          {country === "BR" && (
            <Combobox
              label="Provider"
              value={provider}
              options={[
                { value: "all", label: "All providers" },
                ...[
                  ...new Set(
                    data.connections
                      .filter((c) => c.country_code === "BR")
                      .map((c) => c.provider_code),
                  ),
                ].map((code) => ({ value: code, label: code })),
              ]}
              onChange={(v) => {
                setProvider(v[0] || "all");
                setPage(1);
              }}
            />
          )}
          <div className="field wide-field">
            <label htmlFor="catalog-search">
              Movie name, original name, provider or ID
            </label>
            <input
              id="catalog-search"
              placeholder="Search imported movies…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={exact}
              onChange={(e) => {
                setExact(e.target.checked);
                setPage(1);
              }}
            />
            Exact match
          </label>
        </div>
      </section>
      {error && <ErrorNotice message={error} />}
      <section className="panel" aria-busy={loading}>
        {!country ? (
          <Empty title="Choose a country">
            <p>
              No provider request is made here. This page displays the stored
              catalog only.
            </p>
          </Empty>
        ) : loading ? (
          <div className="loading-panel">Loading catalog…</div>
        ) : (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Movie</th>
                    <th>Provider movie ID</th>
                    <th>Provider</th>
                    <th>Country</th>
                    <th>Group</th>
                    <th>Release date</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {movies.map((movie) => (
                    <tr key={movie.id}>
                      <td>
                        <strong>
                          {movie.name} ({movie.external_id})
                        </strong>
                        {movie.original_name &&
                          movie.original_name !== movie.name && (
                            <small>{movie.original_name}</small>
                          )}
                      </td>
                      <td>{movie.external_id}</td>
                      <td className="nowrap">{movie.provider_code}</td>
                      <td>{movie.country_code}</td>
                      <td>{movie.group_name || "—"}</td>
                      <td>{movie.release_date || "—"}</td>
                      <td>
                        <Status
                          value={movie.active ? "available" : "inactive"}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!movies.length && (
                <Empty title="No movies match">
                  <p>
                    Try clearing the search, or refresh the catalog from Home.
                  </p>
                </Empty>
              )}
            </div>
            <Pagination
              page={page}
              total={total}
              size={50}
              onChange={setPage}
            />
          </>
        )}
      </section>
    </>
  );
}
