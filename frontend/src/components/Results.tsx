"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, localTimestamp, send } from "@/lib/api";
import { apiUrl } from "@/lib/routes";
import { providerMoviesEndpoint } from "@/lib/catalog";
import {
  providerMovieChoice,
  safeExternalUrl,
  type Choice,
} from "@/lib/search";
import type {
  Bootstrap,
  CheckResult,
  Job,
  ProviderMovieRecord,
  Showtime,
  TheaterResult,
} from "@/lib/types";
import { Combobox } from "./Combobox";
import { RemoteCombobox } from "./RemoteCombobox";
import { Empty, ErrorNotice, GenericLogo, Pagination, Status } from "./Shared";

interface Filters {
  q: string;
  exact: boolean;
  chain: string;
  format: string;
  language: string;
  date_from: string;
  links: string;
}
const initialFilters: Filters = {
  q: "",
  exact: false,
  chain: "",
  format: "",
  language: "",
  date_from: "",
  links: "",
};
const optionList = (values: string[], all: string): Choice[] => [
  { value: "", label: all },
  ...values.map((value) => ({ value, label: value })),
];
export function Results({
  view,
  data,
}: {
  view: "showtimes" | "theaters";
  data: Bootstrap;
}) {
  const [country, setCountry] = useState("");
  const [provider, setProvider] = useState("all");
  const [movieIds, setMovieIds] = useState<string[]>([]);
  const [includeArchivedMovies, setIncludeArchivedMovies] = useState(false);
  const [checkId, setCheckId] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [phase, setPhase] = useState("idle");
  const [filters, setFilters] = useState<Filters>(initialFilters);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [rowsLoading, setRowsLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [now, setNow] = useState(Date.now());
  const sequence = useRef(0);
  const working = ["queued", "processing"].includes(phase);
  const valid = ["done", "partial", "completed"].includes(phase) && !!result;
  const query = useMemo(() => {
    const params = new URLSearchParams({
      view,
      page: String(page),
      page_size: "50",
    });
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== "" && value !== false) params.set(key, String(value));
    });
    return params.toString();
  }, [view, page, filters]);
  const handleError = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 410) {
      setPhase(
        /stale|reference data changed/i.test(e.message) ? "stale" : "expired",
      );
      setResult(null);
    }
    setError((e as Error).message);
  }, []);
  useEffect(() => {
    if (!checkId || !valid) return;
    const controller = new AbortController();
    const generation = sequence.current;
    setRowsLoading(true);
    const timer = setTimeout(() => {
      api<CheckResult>(`checks/${checkId}?${query}`, {
        signal: controller.signal,
      })
        .then((next) => {
          if (generation === sequence.current) setResult(next);
        })
        .catch((e) => {
          if (e.name !== "AbortError") handleError(e);
        })
        .finally(() => {
          if (!controller.signal.aborted) setRowsLoading(false);
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [checkId, query, valid, handleError]);
  useEffect(() => {
    if (!job || !["queued", "processing"].includes(job.status)) return;
    const generation = sequence.current;
    const timer = setTimeout(async () => {
      try {
        const next = await api<Job>(`jobs/${job.id}`);
        if (generation !== sequence.current) return;
        setJob(next);
        setPhase(next.status);
        if (["done", "partial", "completed"].includes(next.status)) {
          const loaded = await api<CheckResult>(`checks/${checkId}?${query}`);
          if (generation === sequence.current) {
            setResult(loaded);
            setPhase(loaded.status || next.status);
          }
        } else if (next.status === "failed")
          setError(
            next.message ||
              "No requested provider completed successfully. Retry or load data again.",
          );
      } catch (e) {
        if (generation === sequence.current) {
          setPhase("failed");
          handleError(e);
        }
      }
    }, 1800);
    return () => clearTimeout(timer);
  }, [job, checkId, query, handleError]);
  useEffect(() => {
    if (!result?.expires_at || !valid) return;
    const timer = setInterval(() => {
      const timestamp = Date.now();
      setNow(timestamp);
      if (timestamp >= new Date(result.expires_at!).getTime()) {
        setPhase("expired");
        setResult(null);
        setError("");
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [result?.expires_at, valid]);
  function reset() {
    sequence.current += 1;
    setJob(null);
    setCheckId("");
    setResult(null);
    setPhase("idle");
    setError("");
    setPage(1);
    setFilters(initialFilters);
  }
  function changeFilter<K extends keyof Filters>(key: K, value: Filters[K]) {
    setPage(1);
    setFilters((current) => ({ ...current, [key]: value }));
  }
  async function load() {
    reset();
    setPhase("queued");
    try {
      const providerCodes =
        country === "BR"
          ? provider === "all"
            ? data.connections
                .filter((c) => c.country_code === "BR" && c.enabled)
                .map((c) => c.provider_code)
            : [provider]
          : data.connections
              .filter((c) => c.country_code === country && c.enabled)
              .map((c) => c.provider_code);
      const created = await send<{ job_id: string }>("checks", {
        country_code: country,
        provider_movie_ids: movieIds.map(Number),
        provider_codes: [...new Set(providerCodes)],
      });
      setCheckId(created.job_id);
      setJob({
        id: created.job_id,
        status: "queued",
        message: "Waiting for a worker…",
      });
    } catch (e) {
      setPhase("failed");
      handleError(e);
    }
  }
  async function retry(code: string) {
    setError("");
    try {
      const retried = await send<{ job_id?: string }>(
        `checks/${checkId}/retry`,
        { provider_codes: [code] },
      );
      setPhase("queued");
      setJob({
        id: retried?.job_id || checkId,
        status: "queued",
        message: `Retrying ${code} only. Existing provider expiry times are unchanged.`,
      });
    } catch (e) {
      handleError(e);
    }
  }
  async function cancel() {
    try {
      await api(`checks/${checkId}`, { method: "DELETE" });
      sequence.current += 1;
      setJob(null);
      setResult(null);
      setPhase("cancelled");
    } catch (e) {
      handleError(e);
    }
  }
  async function exportXlsx() {
    setExporting(true);
    setError("");
    try {
      const response = await fetch(
        apiUrl(`checks/${checkId}/export?${query}`),
        {
          credentials: "same-origin",
          cache: "no-store",
        },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new ApiError(
          body.detail || "Export could not be created.",
          response.status,
        );
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const filename = disposition.match(
        /filename\*?=(?:UTF-8'')?["']?([^"';]+)/i,
      )?.[1];
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename
        ? decodeURIComponent(filename).replace(/[\\/]/g, "-")
        : `${view}-${country}-${movieIds.join("-")}.xlsx`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      handleError(e);
    } finally {
      setExporting(false);
    }
  }
  const providers = [
    ...new Set(
      data.connections
        .filter((c) => c.country_code === "BR" && c.enabled)
        .map((c) => c.provider_code),
    ),
  ];
  const secondsLeft = result?.expires_at
    ? Math.max(
        0,
        Math.floor((new Date(result.expires_at).getTime() - now) / 1000),
      )
    : 0;
  const title = view === "showtimes" ? "Showtime data" : "Theaters by movie";
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Fresh provider data</p>
          <h1>{title}</h1>
          <p>
            {view === "showtimes"
              ? "Inspect screenings, formats, languages and usable booking links."
              : "See current theaters and upcoming showtime availability for the selected movie."}
          </p>
        </div>
        <button
          type="button"
          className="button"
          disabled={!valid || rowsLoading || exporting}
          onClick={exportXlsx}
        >
          {exporting ? "Preparing XLSX…" : "↓ Export XLSX"}
        </button>
      </div>
      <section className="panel">
        <div className="fields selection-fields">
          <Combobox
            label="Country"
            value={country}
            required
            options={data.countries
              .filter((c) => c.enabled !== false)
              .map((c) => ({ value: c.code, label: `${c.name} (${c.code})` }))}
            disabled={working}
            placeholder="Choose a country"
            onChange={(values) => {
              reset();
              setCountry(values[0] || "");
              setIncludeArchivedMovies(false);
              setProvider("all");
              setMovieIds([]);
            }}
          />
          {country === "BR" && (
            <Combobox
              label="Provider"
              value={provider}
              options={[
                { value: "all", label: "All providers" },
                ...providers.map((code) => ({ value: code, label: code })),
              ]}
              disabled={working}
              onChange={(values) => {
                reset();
                setProvider(values[0] || "all");
                setMovieIds([]);
              }}
            />
          )}
          <div className="wide-field">
            <RemoteCombobox
              endpoint={
                country
                  ? providerMoviesEndpoint(
                      country,
                      provider,
                      includeArchivedMovies,
                    )
                  : null
              }
              cacheScope={
                country ? `movie-picker:${country}:${provider}` : null
              }
              label={country === "BR" ? "Movies / provider IDs" : "Movie"}
              required
              value={movieIds}
              multiple={country === "BR"}
              mapRecord={(row) =>
                providerMovieChoice(row as unknown as ProviderMovieRecord)
              }
              disabled={!country || working}
              placeholder="Choose imported movie"
              onChange={(values) => {
                reset();
                setMovieIds(values);
              }}
            />
          </div>
          <button
            className="button primary"
            type="button"
            disabled={!country || !movieIds.length || working}
            onClick={load}
          >
            {phase === "expired" || phase === "stale"
              ? "↻ Reload data"
              : "Load data"}
          </button>
        </div>
        {country && (
          <div className="panel-note">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={includeArchivedMovies}
                disabled={working}
                onChange={(event) =>
                  setIncludeArchivedMovies(event.target.checked)
                }
              />
              Include archived movies
            </label>
            <small>
              Archived means absent from the latest discovery catalog, not
              deleted. Existing selections are retained; explicit checks can
              request fresh data for these IDs.
            </small>
          </div>
        )}
        <div className="panel-note">
          {country === "BR"
            ? "Select all English/localized movie entries or variant IDs that belong together. They are fetched in one check; the catalog itself is not changed."
            : "No showtime requests run until you click Load data."}{" "}
          Screenings use the provider/theater’s local time.
        </div>
      </section>
      {error && <ErrorNotice message={error} />}
      {working && (
        <section className="panel loading-panel" role="status">
          <div className="button-row">
            <span className="spinner" />
            <h2>
              {job?.status === "queued"
                ? "Check queued"
                : "Fetching provider data"}
            </h2>
          </div>
          <p>
            {job?.message ||
              "Provider requests are being processed with bounded concurrency."}
          </p>
          {job?.progress !== undefined && (
            <progress value={job.progress} max={100} />
          )}
          <button
            className="button"
            type="button"
            onClick={cancel}
            disabled={!checkId}
          >
            Cancel check
          </button>
        </section>
      )}
      {!working && ["expired", "stale"].includes(phase) && (
        <div className="notice warning">
          <div>
            <strong>
              {phase === "expired"
                ? "These temporary results have expired."
                : "Reference data changed. These results are stale."}
            </strong>
            <p>
              Results and exports are no longer available. Click Reload data to
              make a fresh provider request.
            </p>
          </div>
        </div>
      )}
      {!working && !valid && (
        <section className="panel">
          <Empty
            title={
              phase === "cancelled"
                ? "Check cancelled"
                : phase === "failed"
                  ? "Unable to load a complete result"
                  : phase === "expired" || phase === "stale"
                    ? "Load fresh data to continue"
                    : "Choose a country and movie to begin"
            }
          >
            <p>
              {phase === "idle"
                ? "Showtimes are not stored in the database. Loading explicitly fetches the latest available provider data."
                : "Your movie and country selection has been retained."}
            </p>
          </Empty>
        </section>
      )}
      {!working && phase === "failed" && job?.provider_outcomes && (
        <section className="panel">
          <div className="panel-head">
            <h2>Provider outcomes</h2>
          </div>
          <div className="fields">
            {job.provider_outcomes.map((outcome) => (
              <div key={outcome.provider_code}>
                <strong>{outcome.provider_code}</strong>
                <Status value={outcome.status} />
                <small>{outcome.message}</small>
                {["failed", "partial"].includes(outcome.status) && (
                  <button
                    type="button"
                    className="button small"
                    onClick={() => retry(outcome.provider_code)}
                  >
                    Retry {outcome.provider_code}
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
      {valid && result && (
        <>
          {phase === "partial" && (
            <div className="notice warning">
              <div>
                <strong>Incomplete provider results</strong>
                <p>
                  At least one requested provider failed or returned incomplete
                  data. Counts and exports reflect only received data. Retry the
                  failed or incomplete provider below.
                </p>
              </div>
            </div>
          )}
          <div className="metrics">
            {[
              ["Showtimes", result.metrics.showtimes],
              ["Theaters", result.metrics.theaters],
              ["No usable link", result.metrics.no_usable_link],
              ["IMAX showtimes", result.metrics.imax],
            ].map(([label, value]) => (
              <div className="metric" key={label}>
                <span>{label}</span>
                <strong>{Number(value || 0).toLocaleString()}</strong>
                {label === "No usable link" && (
                  <small>No booking or fallback link</small>
                )}
              </div>
            ))}
          </div>
          <section className="provider-status">
            <div className="provider-outcomes">
              {result.provider_outcomes.map((outcome) => (
                <div key={outcome.provider_code}>
                  <strong>{outcome.provider_code}</strong>
                  <Status value={outcome.status} />
                  <small>
                    {outcome.fetched_at
                      ? `Fetched ${localTimestamp(outcome.fetched_at)}`
                      : outcome.message || "No data received"}
                    {outcome.expires_at
                      ? ` · Expires ${localTimestamp(outcome.expires_at)}`
                      : ""}
                  </small>
                  {["failed", "partial"].includes(outcome.status) && (
                    <button
                      type="button"
                      className="button small"
                      onClick={() => retry(outcome.provider_code)}
                    >
                      Retry {outcome.provider_code}
                    </button>
                  )}
                </div>
              ))}
            </div>
            <small>
              Results expire in {Math.floor(secondsLeft / 60)}:
              {String(secondsLeft % 60).padStart(2, "0")} · Activity does not
              extend expiry.
            </small>
          </section>
          <section className="panel">
            <div className="fields">
              <div className="field wide-field">
                <label htmlFor="results-search">
                  Movie, theater, provider ID or raw format
                </label>
                <input
                  id="results-search"
                  value={filters.q}
                  onChange={(e) => changeFilter("q", e.target.value)}
                  placeholder="Search loaded results…"
                />
              </div>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={filters.exact}
                  onChange={(e) => changeFilter("exact", e.target.checked)}
                />
                Exact match
              </label>
              <Combobox
                label="Theater chain"
                value={filters.chain}
                options={optionList(result.filters.chains, "All chains")}
                onChange={(v) => changeFilter("chain", v[0] || "")}
              />
              <Combobox
                label="Format"
                value={filters.format}
                options={optionList(result.filters.formats, "All formats")}
                onChange={(v) => changeFilter("format", v[0] || "")}
              />
              <Combobox
                label="Language"
                value={filters.language}
                options={optionList(result.filters.languages, "All languages")}
                onChange={(v) => changeFilter("language", v[0] || "")}
              />
              <div className="field">
                <label htmlFor="date-from">Provider local date from</label>
                <input
                  id="date-from"
                  type="date"
                  value={filters.date_from}
                  onChange={(e) => changeFilter("date_from", e.target.value)}
                />
              </div>
              <Combobox
                label="Links"
                value={filters.links}
                options={[
                  { value: "", label: "All link statuses" },
                  { value: "available", label: "Usable link available" },
                  { value: "missing", label: "No usable link" },
                ]}
                onChange={(v) => changeFilter("links", v[0] || "")}
              />
              <button
                className="button small"
                type="button"
                onClick={() => {
                  setFilters(initialFilters);
                  setPage(1);
                }}
              >
                Clear filters
              </button>
            </div>
          </section>
          <section
            className={`panel ${rowsLoading ? "updating" : ""}`}
            aria-busy={rowsLoading}
          >
            <div className="table-scroll">
              <table>
                {view === "showtimes" ? (
                  <>
                    <thead>
                      <tr>
                        <th>Movie</th>
                        <th>Theater</th>
                        <th>Provider local date & time</th>
                        <th>Format & language</th>
                        <th>Booking / fallback links</th>
                        <th>Providers</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(result.rows as Showtime[]).map((row) => (
                        <tr key={row.id}>
                          <td>
                            <strong>{row.movie_name}</strong>
                            <small>{row.country_code}</small>
                          </td>
                          <td>
                            <TheaterCell row={row} />
                          </td>
                          <td>
                            <strong>
                              {row.date} {row.time}
                            </strong>
                            <small>Provider local time · not converted</small>
                          </td>
                          <td>
                            <strong>
                              {row.published_format ||
                                row.raw_format ||
                                "Not supplied"}
                            </strong>
                            {!row.format_mapped && (
                              <Status value="provider format" />
                            )}
                            {row.vip &&
                              !/\bVIP\b/i.test(
                                `${row.published_format || ""} ${row.raw_format}`,
                              ) && <Status value="VIP" />}
                            <small>
                              Raw: {row.raw_format || "Not supplied"}
                            </small>
                            {row.audio_language && (
                              <small>Audio: {row.audio_language}</small>
                            )}
                            {row.subtitle_language && (
                              <small>Subtitles: {row.subtitle_language}</small>
                            )}
                          </td>
                          <td>
                            <div className="links">
                              {(row.links || []).map((link, i) => {
                                const url = safeExternalUrl(link.url);
                                return url ? (
                                  <a
                                    href={url}
                                    key={`${link.name}-${i}`}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="link-button"
                                    title={link.kind}
                                  >
                                    {link.name}
                                    {link.kind !== "booking" && (
                                      <span>
                                        {" "}
                                        · {link.kind.replaceAll("_", " ")}
                                      </span>
                                    )}{" "}
                                    ↗
                                  </a>
                                ) : null;
                              })}
                            </div>
                            {!row.has_usable_link && (
                              <span className="missing">No usable link</span>
                            )}
                          </td>
                          <td>{(row.providers || []).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </>
                ) : (
                  <>
                    <thead>
                      <tr>
                        <th>Movie</th>
                        <th>Theater</th>
                        <th>Country</th>
                        <th>Upcoming showtimes</th>
                        <th>Formats</th>
                        <th>Providers</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(result.rows as TheaterResult[]).map((row) => (
                        <tr key={row.id}>
                          <td>
                            <strong>{row.movie_name}</strong>
                          </td>
                          <td>
                            <TheaterCell row={row} />
                          </td>
                          <td>{row.country_code}</td>
                          <td>
                            <strong>{row.showtimes.toLocaleString()}</strong>
                            <small>First: {row.first || "—"}</small>
                            <small>Last: {row.last || "—"}</small>
                            <small>Provider local time</small>
                          </td>
                          <td>{row.formats.join(", ") || "Not supplied"}</td>
                          <td>{row.providers.join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </>
                )}
              </table>
              {result.rows.length === 0 && (
                <Empty title="No screenings match these filters">
                  <p>
                    Clear a filter or load a different movie. Provider catalogs
                    can list movies that have no upcoming sessions.
                  </p>
                </Empty>
              )}
            </div>
            <Pagination
              page={page}
              total={result.total}
              size={50}
              onChange={setPage}
            />
          </section>
        </>
      )}
    </>
  );
}
function TheaterCell({ row }: { row: Showtime | TheaterResult }) {
  return (
    <div className="theater-cell">
      <GenericLogo name={row.theater_name} />
      <div>
        <strong>{row.theater_name}</strong>
        <small>
          Theater #{row.theater_id}
          {row.chain ? ` · ${row.chain}` : ""}
        </small>
        <small>{[row.address, row.city].filter(Boolean).join(", ")}</small>
      </div>
    </div>
  );
}
