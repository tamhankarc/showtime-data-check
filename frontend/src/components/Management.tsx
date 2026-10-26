"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, items, send } from "@/lib/api";
import { providerMovieIdentifier, providerMovieLabel } from "@/lib/search";
import { referenceListQuery } from "@/lib/reference";
import { catalogWindowValue } from "@/lib/catalog";
import type { Bootstrap, ListPage, RecordData } from "@/lib/types";
import { Combobox } from "./Combobox";
import { Empty, ErrorNotice, GenericLogo, Pagination, Status } from "./Shared";
import { MovieOverrides } from "./MovieOverrides";
import { EditorDialog } from "./EditorDialog";
import { RemoteCombobox } from "./RemoteCombobox";

type Page = "countries" | "movies" | "directory" | "assignments" | "formats";
type Resource =
  | "countries"
  | "connections"
  | "movie-groups"
  | "theaters"
  | "provider-theaters"
  | "format-mappings";
const text = (value: unknown) =>
  value === undefined || value === null ? "" : String(value);
const resources: Record<Page, Resource> = {
  countries: "countries",
  movies: "movie-groups",
  directory: "theaters",
  assignments: "provider-theaters",
  formats: "format-mappings",
};
const titles: Record<Page, string> = {
  countries: "Countries & providers",
  movies: "Movies & Brazil groups",
  directory: "Theater directory",
  assignments: "Provider theaters",
  formats: "Format mappings",
};
const descriptions: Record<Page, string> = {
  countries:
    "Maintain country and provider availability independently from the Admin site.",
  movies:
    "Catalog movies are created by provider refreshes. Review Brazil groups without creating Titles.",
  directory: "Canonical theater details and generic first-character logos.",
  assignments:
    "Review new provider theaters and preserve confirmed canonical assignments.",
  formats:
    "Map raw provider values to published formats. Unmapped values remain visible as received.",
};
export function Management({
  page,
  data,
  onChange,
}: {
  page: Page;
  data: Bootstrap;
  onChange: () => Promise<void>;
}) {
  const [resource, setResource] = useState<Resource>(resources[page]);
  const [records, setRecords] = useState<RecordData[]>([]);
  const [total, setTotal] = useState(0);
  const [country, setCountry] = useState(page === "movies" ? "BR" : "");
  const [provider, setProvider] = useState("");
  const [search, setSearch] = useState("");
  const [exact, setExact] = useState(false);
  const [status, setStatus] = useState("");
  const [showInactiveTheaters, setShowInactiveTheaters] = useState(false);
  const [includeArchivedMovies, setIncludeArchivedMovies] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [editing, setEditing] = useState<RecordData | null>(null);
  const [existingRecord, setExistingRecord] = useState(false);
  const loadSequence = useRef(0);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [selectedMovieIds, setSelectedMovieIds] = useState<string[]>([]);
  const [targetGroup, setTargetGroup] = useState("");
  useEffect(() => {
    setResource(resources[page]);
    setCountry(page === "movies" ? "BR" : "");
    setProvider("");
    setSearch("");
    setCurrentPage(1);
    setEditing(null);
    setError("");
    setSuccess("");
    setStatus("");
    setShowInactiveTheaters(false);
    setIncludeArchivedMovies(false);
  }, [page]);
  const load = useCallback(async () => {
    const generation = ++loadSequence.current;
    setLoading(true);
    setError("");
    const params = referenceListQuery({
      resource,
      q: search,
      exact,
      page: currentPage,
      country,
      provider,
      status,
      showInactiveTheaters,
      includeArchivedMovies,
    });
    try {
      const loaded = await api<ListPage<RecordData>>(`${resource}?${params}`);
      if (generation === loadSequence.current) {
        setRecords(items(loaded));
        setTotal(loaded.total);
      }
    } catch (e) {
      if (generation === loadSequence.current) setError((e as Error).message);
    } finally {
      if (generation === loadSequence.current) setLoading(false);
    }
  }, [
    resource,
    country,
    search,
    exact,
    currentPage,
    provider,
    status,
    showInactiveTheaters,
    includeArchivedMovies,
  ]);
  useEffect(() => {
    const timer = setTimeout(
      () => {
        void load();
      },
      search ? 250 : 0,
    );
    return () => clearTimeout(timer);
  }, [load]);
  const providers = [
    ...new Set([
      ...data.connections.map((c) => c.provider_code),
      ...records.map((row) => text(row.provider_code)).filter(Boolean),
    ]),
  ];
  const visible = records;
  function open(row?: RecordData) {
    if (resource === "movie-groups" && row?.country_code !== "BR") {
      setError(
        "Grouping is available only for Brazil. Other countries use provider movie IDs directly.",
      );
      return;
    }
    setExistingRecord(!!row);
    setFormError("");
    setSuccess("");
    setSelectedMovieIds([]);
    setTargetGroup("");
    const timezone =
      data.countries.find((c) => c.code === country)?.timezone || "UTC";
    setEditing(
      row
        ? { ...row }
        : resource === "countries"
          ? { code: "", name: "", timezone: "UTC", enabled: true }
          : resource === "connections"
            ? {
                country_code: country || "",
                provider_code: country === "BR" ? "INGRESSO" : "IS",
                provider_country_code: "",
                timezone,
                enabled: true,
                catalog_window_days: null,
              }
            : resource === "theaters"
              ? {
                  country_code: country || "",
                  name: "",
                  address: "",
                  city: "",
                  region: "",
                  postal_code: "",
                  chain: "",
                  website: "",
                  latitude: null,
                  longitude: null,
                  timezone,
                  active: true,
                }
              : {
                  provider_code: provider || "IS",
                  country_code: country || "",
                  raw_format: "",
                  published_format: "",
                  audio_language: "",
                  subtitle_language: "",
                  is_dubbed: false,
                  is_subtitled: false,
                  is_luxury: false,
                },
    );
  }
  function field(name: string, value: unknown) {
    setEditing((current) => (current ? { ...current, [name]: value } : null));
  }
  async function saved() {
    setEditing(null);
    setSuccess(
      "Saved. Affected temporary results have been invalidated; load fresh data before exporting them.",
    );
    await Promise.all([load(), onChange()]);
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setSaving(true);
    setFormError("");
    try {
      let body: RecordData;
      if (resource === "provider-theaters")
        body = {
          theater_id: editing.theater_id ? Number(editing.theater_id) : null,
          review_status: editing.review_status || "confirmed",
        };
      else if (resource === "theaters") {
        body = Object.fromEntries(
          [
            "country_code",
            "name",
            "address",
            "city",
            "region",
            "postal_code",
            "latitude",
            "longitude",
            "website",
            "chain",
            "timezone",
            "active",
          ].map((key) => [
            key,
            ["latitude", "longitude"].includes(key)
              ? editing[key] === "" || editing[key] == null
                ? null
                : Number(editing[key])
              : editing[key],
          ]),
        );
      } else if (resource === "countries")
        body = Object.fromEntries(
          ["code", "name", "timezone", "enabled"].map((key) => [
            key,
            editing[key],
          ]),
        );
      else if (resource === "connections")
        body = Object.fromEntries(
          [
            "country_code",
            "provider_code",
            "provider_country_code",
            "enabled",
            "timezone",
          ].map((key) => [key, editing[key]]),
        );
      else
        body = Object.fromEntries(
          [
            "provider_code",
            "country_code",
            "raw_format",
            "published_format",
            "audio_language",
            "subtitle_language",
            "is_dubbed",
            "is_subtitled",
            "is_luxury",
          ].map((key) => [key, editing[key]]),
        );
      if (resource === "connections" && editing.provider_code === "IS")
        body.catalog_window_days = catalogWindowValue(
          editing.catalog_window_days,
        );
      const existing = existingRecord;
      const id = resource === "countries" ? editing.code : editing.id;
      await send(
        existing ? `${resource}/${encodeURIComponent(text(id))}` : resource,
        body,
        existing ? "PATCH" : "POST",
      );
      await saved();
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  async function move(split: boolean) {
    if (!selectedMovieIds.length || (!split && !targetGroup)) return;
    setSaving(true);
    setFormError("");
    try {
      await send("movie-groups/move", {
        provider_movie_ids: selectedMovieIds.map(Number),
        target_group_id: split ? null : Number(targetGroup),
      });
      await saved();
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  async function confirmNewTheater() {
    if (!editing || editing.theater_id) return;
    setSaving(true);
    setFormError("");
    try {
      const created = await send<{ id: number }>("theaters", {
        country_code: editing.country_code,
        name: editing.name,
        address: editing.address || null,
        city: editing.city || null,
        region: editing.region || null,
        postal_code: editing.postal_code || null,
        latitude: editing.latitude ?? null,
        longitude: editing.longitude ?? null,
        website: editing.website || null,
        chain: editing.chain || null,
        timezone:
          data.countries.find(
            (country) => country.code === editing.country_code,
          )?.timezone || "UTC",
        active: true,
      });
      setEditing((current) =>
        current ? { ...current, theater_id: created.id } : null,
      );
      await send(
        `provider-theaters/${editing.id}`,
        { theater_id: created.id, review_status: "confirmed" },
        "PATCH",
      );
      await saved();
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const isAddable = [
    "countries",
    "connections",
    "theaters",
    "format-mappings",
  ].includes(resource);
  const addLabel =
    resource === "countries"
      ? "Add country"
      : resource === "connections"
        ? "Add connection"
        : resource === "theaters"
          ? "Add theater"
          : "Add mapping";
  const countryChoices = data.countries.map((c) => ({
    value: c.code,
    label: `${c.name} (${c.code})`,
  }));
  const formCountryChoices = [
    {
      value: "",
      label:
        resource === "format-mappings" ? "All countries" : "Choose a country",
    },
    ...countryChoices,
  ];
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Manage reference data</p>
          <h1>{titles[page]}</h1>
          <p>{descriptions[page]}</p>
        </div>
        {isAddable && (
          <button className="button primary" onClick={() => open()}>
            ＋ {addLabel}
          </button>
        )}
      </div>
      {page === "countries" && (
        <div
          className="tabs"
          role="group"
          aria-label="Countries or connections"
        >
          <button
            className={resource === "countries" ? "active" : ""}
            onClick={() => {
              setResource("countries");
              setCurrentPage(1);
            }}
            type="button"
          >
            Countries
          </button>
          <button
            className={resource === "connections" ? "active" : ""}
            onClick={() => {
              setResource("connections");
              setCurrentPage(1);
            }}
            type="button"
          >
            Provider connections
          </button>
        </div>
      )}
      {success && (
        <div className="notice" role="status">
          {success}
        </div>
      )}
      {error && <ErrorNotice message={error} retry={load} />}
      <section className="panel">
        <div className="fields">
          <div className="field wide-field">
            <label htmlFor="management-search">
              {resource === "theaters"
                ? "Name, provider ID, address, city or postal code"
                : resource === "provider-theaters"
                  ? "Name, ID, address, city or postal code"
                  : "Search reference data"}
            </label>
            <input
              id="management-search"
              value={search}
              placeholder={
                resource === "theaters"
                  ? "Name, provider ID, address, city or postal code…"
                  : "Search…"
              }
              onChange={(e) => {
                setSearch(e.target.value);
                setCurrentPage(1);
              }}
            />
          </div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={exact}
              onChange={(e) => {
                setExact(e.target.checked);
                setCurrentPage(1);
              }}
            />
            Exact match
          </label>
          {resource === "theaters" && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={showInactiveTheaters}
                onChange={(event) => {
                  setShowInactiveTheaters(event.target.checked);
                  setCurrentPage(1);
                }}
              />
              Show inactive theaters
            </label>
          )}
          {resource === "movie-groups" && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={includeArchivedMovies}
                onChange={(event) => {
                  setIncludeArchivedMovies(event.target.checked);
                  setCurrentPage(1);
                }}
              />
              Include archived movies
            </label>
          )}
          {resource !== "countries" && (
            <Combobox
              label="Country"
              options={[
                { value: "", label: "All countries" },
                ...countryChoices,
              ]}
              value={country}
              onChange={(v) => {
                setCountry(v[0] || "");
                setCurrentPage(1);
              }}
            />
          )}
          {[
            "provider-theaters",
            "format-mappings",
            "movie-groups",
            "connections",
          ].includes(resource) && (
            <Combobox
              label="Provider"
              value={provider}
              options={[
                { value: "", label: "All providers" },
                ...providers.map((value) => ({ value, label: value })),
              ]}
              onChange={(v) => {
                setProvider(v[0] || "");
                setCurrentPage(1);
              }}
            />
          )}
          {resource === "provider-theaters" && (
            <Combobox
              label="Review status"
              value={status}
              options={[
                { value: "", label: "All statuses" },
                { value: "needs_review", label: "Needs review" },
                { value: "confirmed", label: "Confirmed" },
                { value: "ignored", label: "Ignored" },
              ]}
              onChange={(v) => {
                setStatus(v[0] || "");
                setCurrentPage(1);
              }}
            />
          )}
        </div>
      </section>
      <section className="panel" aria-busy={loading}>
        {loading ? (
          <div className="loading-panel">
            <span className="spinner" /> Loading reference data…
          </div>
        ) : (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    {resource === "countries" ? (
                      <>
                        <th>Country</th>
                        <th>Timezone</th>
                        <th>Status</th>
                      </>
                    ) : resource === "connections" ? (
                      <>
                        <th>Country</th>
                        <th>Provider</th>
                        <th>Provider country code</th>
                        <th>Status</th>
                      </>
                    ) : resource === "movie-groups" ? (
                      <>
                        <th>Movie group</th>
                        <th>Country</th>
                        <th>Provider movie names / IDs</th>
                        <th>Status</th>
                      </>
                    ) : resource === "theaters" ? (
                      <>
                        <th>Theater</th>
                        <th>Provider IDs</th>
                        <th>Chain</th>
                        <th>Location</th>
                        <th>Coordinates / website</th>
                        <th>Status</th>
                      </>
                    ) : resource === "provider-theaters" ? (
                      <>
                        <th>Observed theater</th>
                        <th>Provider</th>
                        <th>Observed location</th>
                        <th>Canonical theater</th>
                        <th>Status</th>
                      </>
                    ) : (
                      <>
                        <th>Raw provider format</th>
                        <th>Provider</th>
                        <th>Country</th>
                        <th>Published format</th>
                      </>
                    )}
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row, index) => (
                    <tr key={text(row.id || row.code || index)}>
                      {resource === "countries" ? (
                        <>
                          <td>
                            <strong>{text(row.name)}</strong>
                            <small>{text(row.code)}</small>
                          </td>
                          <td>{text(row.timezone)}</td>
                          <td>
                            <Status
                              value={
                                row.enabled === false ? "disabled" : "enabled"
                              }
                            />
                          </td>
                        </>
                      ) : resource === "connections" ? (
                        <>
                          <td>{text(row.country_code)}</td>
                          <td>{text(row.provider_code)}</td>
                          <td>{text(row.provider_country_code) || "—"}</td>
                          <td>
                            <Status
                              value={
                                row.enabled === false ? "disabled" : "enabled"
                              }
                            />
                          </td>
                        </>
                      ) : resource === "movie-groups" ? (
                        <>
                          <td>
                            <strong>{text(row.name)}</strong>
                            <small>Group #{text(row.id)}</small>
                          </td>
                          <td>{text(row.country_code)}</td>
                          <td>
                            {((row.provider_ids || []) as RecordData[]).map(
                              (id) => (
                                <small key={text(id.id)}>
                                  {providerMovieLabel(id)} ·{" "}
                                  {text(id.provider_code)}
                                  {id.active === false
                                    ? " · Archived"
                                    : " · Available"}
                                </small>
                              ),
                            )}
                          </td>
                          <td>
                            <Status
                              value={
                                row.active === false ? "archived" : "active"
                              }
                            />
                          </td>
                        </>
                      ) : resource === "theaters" ? (
                        <>
                          <td>
                            <div className="theater-cell">
                              <GenericLogo name={text(row.name)} />
                              <div>
                                <strong>{text(row.name)}</strong>
                                <small>{text(row.country_code)}</small>
                                <small>Generic logo</small>
                              </div>
                            </div>
                          </td>
                          <td>
                            {Array.isArray(row.provider_ids) &&
                            row.provider_ids.length
                              ? (row.provider_ids as RecordData[]).map(
                                  (identity) => (
                                    <div
                                      key={`${text(identity.provider_code)}-${text(identity.external_id)}`}
                                    >
                                      <strong>
                                        {text(identity.provider_code)}
                                      </strong>
                                      <small>
                                        {text(identity.external_id)}
                                      </small>
                                    </div>
                                  ),
                                )
                              : "—"}
                          </td>
                          <td>{text(row.chain) || "—"}</td>
                          <td>
                            {text(row.address)}
                            <small>
                              {[row.city, row.region, row.postal_code]
                                .filter(Boolean)
                                .map(text)
                                .join(", ")}
                            </small>
                          </td>
                          <td>
                            {row.latitude != null && row.longitude != null
                              ? `${text(row.latitude)}, ${text(row.longitude)}`
                              : "Not supplied"}
                            <small>
                              {text(row.website) || "No theater URL"}
                            </small>
                          </td>
                          <td>
                            <Status
                              value={
                                row.active === false ? "inactive" : "active"
                              }
                            />
                          </td>
                        </>
                      ) : resource === "provider-theaters" ? (
                        <>
                          <td>
                            <strong>{text(row.name)}</strong>
                            <small>External ID {text(row.external_id)}</small>
                          </td>
                          <td>
                            {text(row.provider_code)}
                            <small>{text(row.country_code)}</small>
                          </td>
                          <td>
                            {text(row.address)}
                            <small>{text(row.city)}</small>
                          </td>
                          <td>
                            {row.theater_id ? (
                              <>
                                <strong>
                                  #{text(row.theater_id)} ·{" "}
                                  {text(row.theater_name)}
                                </strong>
                              </>
                            ) : (
                              "Unassigned"
                            )}
                          </td>
                          <td>
                            <Status value={text(row.review_status)} />
                          </td>
                        </>
                      ) : (
                        <>
                          <td>
                            <strong>{text(row.raw_format)}</strong>
                          </td>
                          <td>{text(row.provider_code)}</td>
                          <td>{text(row.country_code) || "All countries"}</td>
                          <td>
                            {text(row.published_format) ||
                              "Raw provider fallback"}
                          </td>
                        </>
                      )}
                      <td>
                        <button
                          className="button small"
                          type="button"
                          onClick={() => open(row)}
                        >
                          {resource === "provider-theaters"
                            ? "Review"
                            : resource === "movie-groups"
                              ? "Review IDs"
                              : "Edit"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!visible.length && (
                <Empty title="No matching reference data">
                  <p>
                    {records.length
                      ? "Try clearing the filters."
                      : "Complete the one-time data copy or refresh the provider catalogs."}
                  </p>
                </Empty>
              )}
            </div>
            <Pagination
              page={currentPage}
              total={total}
              size={50}
              onChange={setCurrentPage}
            />
          </>
        )}
      </section>
      {editing && (
        <EditorDialog
          title={
            resource === "movie-groups"
              ? "Review imported provider IDs"
              : resource === "provider-theaters"
                ? "Review theater assignment"
                : `${existingRecord ? "Edit" : "Add"} ${resource.replaceAll("-", " ")}`
          }
          onClose={() => {
            if (!saving) setEditing(null);
          }}
        >
          {formError && <ErrorNotice message={formError} />}
          {resource === "movie-groups" ? (
            <>
              <p className="muted">
                Move selected imported IDs to an existing group, or separate
                them into a newly named group based on their provider data.
                Provider IDs and original names are not changed.
              </p>
              <div className="group-members">
                {((editing.provider_ids || []) as RecordData[]).map((id) => (
                  <label className="member" key={text(id.id)}>
                    <input
                      type="checkbox"
                      checked={selectedMovieIds.includes(text(id.id))}
                      onChange={(e) =>
                        setSelectedMovieIds((current) =>
                          e.target.checked
                            ? [...current, text(id.id)]
                            : current.filter((value) => value !== text(id.id)),
                        )
                      }
                    />
                    <span>
                      <strong>{providerMovieLabel(id)}</strong>
                      <small>
                        {text(id.provider_code)}
                        {id.active === false ? " · Archived" : " · Available"}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
              <RemoteCombobox
                endpoint={`movie-groups?country=${encodeURIComponent(text(editing.country_code))}&include_inactive=true`}
                label="Move to imported movie group"
                value={targetGroup}
                mapRecord={(row) => ({
                  value: text(row.id),
                  label: `${text(row.name)} (#${text(row.id)})${row.active === false ? " · Archived" : ""}`,
                  detail: ((row.provider_ids || []) as RecordData[])
                    .map(
                      (id) =>
                        `${text(id.provider_code)} ${providerMovieIdentifier(id)}`,
                    )
                    .join(", "),
                })}
                onChange={(v) => setTargetGroup(v[0] || "")}
                placeholder="Choose an existing group"
              />
              <div className="dialog-actions">
                <button
                  type="button"
                  className="button"
                  disabled={saving || !selectedMovieIds.length}
                  onClick={() => move(true)}
                >
                  Separate selected IDs
                </button>
                <button
                  type="button"
                  className="button primary"
                  disabled={saving || !selectedMovieIds.length || !targetGroup}
                  onClick={() => move(false)}
                >
                  {saving ? "Saving…" : "Move selected IDs"}
                </button>
              </div>
            </>
          ) : (
            <form onSubmit={save}>
              <div className="form-grid">
                {resource === "countries" && (
                  <>
                    <TextField
                      label="Country code"
                      value={editing.code}
                      onChange={(v) => field("code", v.toUpperCase())}
                      required
                      disabled={existingRecord}
                      maxLength={3}
                    />
                    <TextField
                      label="Country name"
                      value={editing.name}
                      onChange={(v) => field("name", v)}
                      required
                    />
                    <TextField
                      label="IANA timezone"
                      value={editing.timezone}
                      onChange={(v) => field("timezone", v)}
                      required
                    />
                    <CheckField
                      label="Enabled"
                      checked={editing.enabled !== false}
                      onChange={(v) => field("enabled", v)}
                    />
                  </>
                )}
                {resource === "connections" && (
                  <>
                    <Combobox
                      label="Country"
                      required
                      options={countryChoices}
                      value={text(editing.country_code)}
                      onChange={(v) => field("country_code", v[0] || "")}
                    />
                    <Combobox
                      label="Provider"
                      required
                      options={["IS", "INGRESSO", "CINEMARK", "VELOX"].map(
                        (value) => ({ value, label: value }),
                      )}
                      value={text(editing.provider_code)}
                      onChange={(v) => field("provider_code", v[0] || "")}
                    />
                    <TextField
                      label="Provider country code"
                      value={editing.provider_country_code}
                      onChange={(v) => field("provider_country_code", v)}
                    />
                    <TextField
                      label="IANA timezone"
                      value={editing.timezone}
                      onChange={(v) => field("timezone", v)}
                    />
                    {editing.provider_code === "IS" && (
                      <div className="field full-span">
                        <label htmlFor="catalog-window-days">
                          IS catalog window in days
                        </label>
                        <input
                          id="catalog-window-days"
                          type="number"
                          min="1"
                          max="3650"
                          step="1"
                          placeholder="Unlimited (leave blank)"
                          value={
                            editing.catalog_window_days == null ||
                            Number(editing.catalog_window_days) === 0
                              ? ""
                              : text(editing.catalog_window_days)
                          }
                          onChange={(event) =>
                            field("catalog_window_days", event.target.value)
                          }
                        />
                        <small>
                          Leave blank for unlimited discovery. Limits movie
                          discovery only; explicit movie checks remain
                          unrestricted. Older films with current screenings are
                          not filtered by release date.
                        </small>
                      </div>
                    )}
                    <CheckField
                      label="Enabled"
                      checked={editing.enabled !== false}
                      onChange={(v) => field("enabled", v)}
                    />
                    <p className="form-help">
                      Disabling preserves reference records and manual
                      assignments. Provider credentials are configured securely
                      on the server, not in this form.
                    </p>
                  </>
                )}
                {resource === "theaters" && (
                  <>
                    <Combobox
                      label="Country"
                      value={text(editing.country_code)}
                      options={countryChoices}
                      onChange={(v) => field("country_code", v[0] || "")}
                      required
                    />
                    <TextField
                      label="Theater name"
                      value={editing.name}
                      onChange={(v) => field("name", v)}
                      required
                    />
                    <TextField
                      label="Address"
                      value={editing.address}
                      onChange={(v) => field("address", v)}
                    />
                    <TextField
                      label="City"
                      value={editing.city}
                      onChange={(v) => field("city", v)}
                    />
                    <TextField
                      label="State / region"
                      value={editing.region}
                      onChange={(v) => field("region", v)}
                    />
                    <TextField
                      label="Postal code"
                      value={editing.postal_code}
                      onChange={(v) => field("postal_code", v)}
                    />
                    <TextField
                      label="Chain"
                      value={editing.chain}
                      onChange={(v) => field("chain", v)}
                    />
                    <TextField
                      label="Theater URL"
                      type="url"
                      value={editing.website}
                      onChange={(v) => field("website", v)}
                    />
                    <TextField
                      label="Latitude"
                      type="number"
                      value={editing.latitude}
                      onChange={(v) => field("latitude", v)}
                      min={-90}
                      max={90}
                    />
                    <TextField
                      label="Longitude"
                      type="number"
                      value={editing.longitude}
                      onChange={(v) => field("longitude", v)}
                      min={-180}
                      max={180}
                    />
                    <TextField
                      label="IANA timezone"
                      value={editing.timezone}
                      onChange={(v) => field("timezone", v)}
                    />
                    <CheckField
                      label="Active"
                      checked={editing.active !== false}
                      onChange={(v) => field("active", v)}
                    />
                  </>
                )}
                {resource === "provider-theaters" && (
                  <>
                    <div className="full-span notice">
                      <div>
                        <strong>
                          {text(editing.name)} ({text(editing.external_id)})
                        </strong>
                        <small>
                          {text(editing.provider_code)} ·{" "}
                          {text(editing.country_code)} ·{" "}
                          {[editing.address, editing.city]
                            .filter(Boolean)
                            .map(text)
                            .join(", ")}
                        </small>
                      </div>
                    </div>
                    <div className="full-span">
                      <RemoteCombobox
                        endpoint={`theaters?country=${encodeURIComponent(text(editing.country_code))}`}
                        label="Canonical theater"
                        value={text(editing.theater_id)}
                        placeholder="Choose existing theater"
                        seedOptions={
                          editing.theater_id
                            ? [
                                {
                                  value: text(editing.theater_id),
                                  label: `${text(editing.theater_name || editing.name)} (#${text(editing.theater_id)})`,
                                },
                              ]
                            : []
                        }
                        mapRecord={(theater) => ({
                          value: text(theater.id),
                          label: `${text(theater.name)} (#${text(theater.id)})`,
                          detail: [theater.address, theater.city]
                            .filter(Boolean)
                            .map(text)
                            .join(", "),
                        })}
                        onChange={(v) => field("theater_id", v[0] || null)}
                      />
                    </div>
                    <Combobox
                      label="Review status"
                      value={text(editing.review_status || "needs_review")}
                      options={[
                        { value: "needs_review", label: "Needs review" },
                        { value: "confirmed", label: "Confirmed" },
                        {
                          value: "ignored",
                          label: "Ignored / not a valid theater",
                        },
                      ]}
                      onChange={(v) => field("review_status", v[0])}
                    />
                    <div className="button-row">
                      <button
                        type="button"
                        className="button small"
                        onClick={() => field("theater_id", null)}
                      >
                        Remove assignment
                      </button>
                      {!!editing.theater_id && (
                        <button
                          type="button"
                          className="button small"
                          onClick={() => field("review_status", "confirmed")}
                        >
                          Confirm current theater
                        </button>
                      )}
                      {!editing.theater_id && (
                        <button
                          type="button"
                          className="button small"
                          disabled={saving}
                          onClick={confirmNewTheater}
                        >
                          Confirm as new theater
                        </button>
                      )}
                    </div>
                    <p className="form-help full-span">
                      Choose a different canonical theater to combine providers
                      at one location. Confirm keeps the provider’s own name and
                      ID unchanged. Use Confirm as new theater only if there is
                      no existing canonical location to assign.
                    </p>
                  </>
                )}
                {resource === "format-mappings" && (
                  <>
                    <Combobox
                      label="Provider"
                      value={text(editing.provider_code)}
                      options={[
                        ...new Set([
                          "IS",
                          "INGRESSO",
                          "CINEMARK",
                          "VELOX",
                          ...providers,
                        ]),
                      ].map((value) => ({ value, label: value }))}
                      onChange={(v) => field("provider_code", v[0])}
                      required
                    />
                    <Combobox
                      label="Country scope"
                      value={text(editing.country_code)}
                      options={formCountryChoices}
                      onChange={(v) => field("country_code", v[0] || "")}
                    />
                    <TextField
                      label="Raw provider format"
                      value={editing.raw_format}
                      onChange={(v) => field("raw_format", v)}
                      required
                    />
                    <TextField
                      label="Published format label"
                      value={editing.published_format}
                      onChange={(v) => field("published_format", v)}
                      required
                    />
                    <div className="full-span">
                      <RemoteCombobox
                        endpoint="format-mappings"
                        label="Or choose an existing published label"
                        value={text(editing.published_format)}
                        seedOptions={
                          editing.published_format
                            ? [
                                {
                                  value: text(editing.published_format),
                                  label: text(editing.published_format),
                                },
                              ]
                            : []
                        }
                        mapRecord={(row) => ({
                          value: text(row.published_format),
                          label: text(row.published_format),
                          detail: `${text(row.provider_code)} · ${text(row.raw_format)}`,
                        })}
                        onChange={(v) => field("published_format", v[0])}
                        placeholder="Search existing published formats"
                      />
                    </div>
                    <p className="form-help full-span">
                      The raw provider value remains available for inspection. A
                      country-specific mapping takes priority over a
                      provider-wide mapping.
                    </p>
                  </>
                )}
                {resource === "format-mappings" && (
                  <>
                    <TextField
                      label="Audio language (optional)"
                      value={editing.audio_language}
                      onChange={(v) => field("audio_language", v)}
                    />
                    <TextField
                      label="Subtitle language (optional)"
                      value={editing.subtitle_language}
                      onChange={(v) => field("subtitle_language", v)}
                    />
                    <CheckField
                      label="Dubbed"
                      checked={!!editing.is_dubbed}
                      onChange={(v) => field("is_dubbed", v)}
                    />
                    <CheckField
                      label="Subtitled"
                      checked={!!editing.is_subtitled}
                      onChange={(v) => field("is_subtitled", v)}
                    />
                    <CheckField
                      label="Luxury / VIP"
                      checked={!!editing.is_luxury}
                      onChange={(v) => field("is_luxury", v)}
                    />
                  </>
                )}
              </div>
              <div className="notice warning">
                <small>
                  Saving reference changes invalidates affected temporary check
                  results. This does not fetch showtimes automatically.
                </small>
              </div>
              <div className="dialog-actions">
                <button
                  type="button"
                  className="button"
                  disabled={saving}
                  onClick={() => setEditing(null)}
                >
                  Cancel
                </button>
                <button
                  className="button primary"
                  type="submit"
                  disabled={saving}
                >
                  {saving ? "Saving…" : "Save changes"}
                </button>
              </div>
            </form>
          )}
        </EditorDialog>
      )}
      {page === "formats" && <MovieOverrides data={data} onChange={onChange} />}
    </>
  );
}
function TextField({
  label,
  value,
  onChange,
  type = "text",
  required,
  disabled,
  maxLength,
  min,
  max,
}: {
  label: string;
  value: unknown;
  onChange: (value: string) => void;
  type?: string;
  required?: boolean;
  disabled?: boolean;
  maxLength?: number;
  min?: number;
  max?: number;
}) {
  const id = `field-${label.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <div className="field">
      <label htmlFor={id}>
        {label}
        {required ? " *" : ""}
      </label>
      <input
        id={id}
        type={type}
        value={text(value)}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        disabled={disabled}
        maxLength={maxLength}
        min={min}
        max={max}
        step={type === "number" ? "any" : undefined}
      />
    </div>
  );
}
function CheckField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="checkbox-label">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}
