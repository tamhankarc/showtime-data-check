"use client";
import { useCallback, useEffect, useState } from "react";
import { api, items, send } from "@/lib/api";
import { providerMovieIdentifier } from "@/lib/search";
import type { Bootstrap, ListPage, RecordData } from "@/lib/types";
import { Combobox } from "./Combobox";
import { EditorDialog } from "./EditorDialog";
import { RemoteCombobox } from "./RemoteCombobox";
import { Empty, ErrorNotice, Pagination, Status } from "./Shared";
export function MovieOverrides({
  data,
  onChange,
}: {
  data: Bootstrap;
  onChange: () => Promise<void>;
}) {
  const [overrides, setOverrides] = useState<RecordData[]>([]);
  const [total, setTotal] = useState(0);
  const [country, setCountry] = useState("");
  const [search, setSearch] = useState("");
  const [exact, setExact] = useState(false);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState<RecordData | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      const params = new URLSearchParams({
        country,
        q: search,
        exact: String(exact),
        page: String(page),
        page_size: "50",
      });
      const rows = await api<ListPage<RecordData>>(
        `movie-format-overrides?${params}`,
      );
      setOverrides(items(rows));
      setTotal(rows.total);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [country, search, exact, page]);
  useEffect(() => {
    const timer = setTimeout(
      () => {
        void load();
      },
      search ? 250 : 0,
    );
    return () => clearTimeout(timer);
  }, [load]);
  const filtered: (RecordData & {
    movie_name: string;
    country_code: string;
    raw_format: string;
    provider_code: string;
  })[] = overrides.map((row) => ({
    ...row,
    movie_name: String(row.group_name || ""),
    country_code: String(row.country_code || ""),
    raw_format: String(row.raw_format || ""),
    provider_code: String(row.provider_code || ""),
  }));
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setSaving(true);
    setFormError("");
    try {
      await send(
        editing.id
          ? `movie-format-overrides/${editing.id}`
          : "movie-format-overrides",
        {
          group_id: Number(editing.group_id),
          format_mapping_id: Number(editing.format_mapping_id),
          published_format: editing.published_format,
          active: editing.active !== false,
        },
        editing.id ? "PATCH" : "POST",
      );
      setEditing(null);
      setMessage(
        "Movie-specific mapping saved. Affected checks must be reloaded.",
      );
      await Promise.all([load(), onChange()]);
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>Movie-specific published formats</h2>
          <small>
            Additional published format for a particular imported movie. Base
            raw-format resolution remains unchanged.
          </small>
        </div>
        <button
          className="button small"
          type="button"
          onClick={() => {
            setFormError("");
            setEditing({
              group_id: "",
              format_mapping_id: "",
              published_format: "",
              active: true,
            });
          }}
        >
          Add movie-specific mapping
        </button>
      </div>
      {error && (
        <div className="fields">
          <ErrorNotice message={error} retry={load} />
        </div>
      )}
      {message && (
        <div className="fields">
          <div className="notice" role="status">
            {message}
          </div>
        </div>
      )}
      <div className="fields">
        <div className="field wide-field">
          <label htmlFor="override-search">
            Search movie-specific mappings
          </label>
          <input
            id="override-search"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Movie, raw or published format…"
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
        <Combobox
          label="Country"
          value={country}
          options={[
            { value: "", label: "All countries" },
            ...data.countries.map((c) => ({
              value: c.code,
              label: `${c.name} (${c.code})`,
            })),
          ]}
          onChange={(v) => {
            setCountry(v[0] || "");
            setPage(1);
          }}
        />
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Movie group</th>
              <th>Country</th>
              <th>Provider / raw format</th>
              <th>Additional published format</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => (
              <tr key={String(row.id)}>
                <td>{row.movie_name}</td>
                <td>{row.country_code}</td>
                <td>
                  {row.raw_format}
                  <small>{row.provider_code}</small>
                </td>
                <td>{String(row.published_format)}</td>
                <td>
                  <Status
                    value={row.active === false ? "inactive" : "active"}
                  />
                </td>
                <td>
                  <button
                    className="button small"
                    onClick={() => {
                      setFormError("");
                      setEditing(row);
                    }}
                  >
                    Edit
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!filtered.length && (
          <Empty title="No movie-specific mappings">
            <p>
              Base mappings apply normally. Add an exception only when a
              selected movie needs another published format.
            </p>
          </Empty>
        )}
      </div>
      <Pagination page={page} total={total} size={50} onChange={setPage} />
      {editing && (
        <EditorDialog
          title="Movie-specific format"
          onClose={() => {
            if (!saving) setEditing(null);
          }}
        >
          {formError && <ErrorNotice message={formError} />}
          <form onSubmit={save}>
            <div className="form-grid">
              <div className="full-span">
                <RemoteCombobox
                  endpoint="movie-groups?include_inactive=true"
                  label="Imported movie group"
                  required
                  value={String(editing.group_id || "")}
                  seedOptions={
                    editing.group_id
                      ? [
                          {
                            value: String(editing.group_id),
                            label: `${String(editing.group_name || "")} · ${String(editing.country_code || "")}`,
                          },
                        ]
                      : []
                  }
                  mapRecord={(group) => ({
                    value: String(group.id),
                    label: `${group.name} · ${group.country_code} (#${group.id})`,
                    detail: ((group.provider_ids || []) as RecordData[])
                      .map(
                        (id) =>
                          `${id.provider_code} (${providerMovieIdentifier(id)})`,
                      )
                      .join(", "),
                  })}
                  onChange={(v, records) =>
                    setEditing({
                      ...editing,
                      group_id: v[0],
                      group_name: records[0]?.name,
                      country_code: records[0]?.country_code,
                      format_mapping_id: "",
                    })
                  }
                />
              </div>
              <div className="full-span">
                <RemoteCombobox
                  endpoint="format-mappings"
                  label="Base raw-format mapping"
                  required
                  value={String(editing.format_mapping_id || "")}
                  seedOptions={
                    editing.format_mapping_id
                      ? [
                          {
                            value: String(editing.format_mapping_id),
                            label: `${String(editing.raw_format || "")} · ${String(editing.provider_code || "")}`,
                          },
                        ]
                      : []
                  }
                  mapRecord={(mapping) => ({
                    value: String(mapping.id),
                    label: `${mapping.raw_format} → ${mapping.published_format}`,
                    detail: `${mapping.provider_code} · ${mapping.country_code || "All countries"}`,
                  })}
                  onChange={(v) =>
                    setEditing({ ...editing, format_mapping_id: v[0] })
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="override-label">
                  Additional published format *
                </label>
                <input
                  id="override-label"
                  value={String(editing.published_format || "")}
                  onChange={(e) =>
                    setEditing({ ...editing, published_format: e.target.value })
                  }
                  required
                />
              </div>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={editing.active !== false}
                  onChange={(e) =>
                    setEditing({ ...editing, active: e.target.checked })
                  }
                />
                Active
              </label>
            </div>
            <div className="dialog-actions">
              <button
                className="button"
                type="button"
                disabled={saving}
                onClick={() => setEditing(null)}
              >
                Cancel
              </button>
              <button
                className="button primary"
                type="submit"
                disabled={
                  saving || !editing.group_id || !editing.format_mapping_id
                }
              >
                {saving ? "Saving…" : "Save mapping"}
              </button>
            </div>
          </form>
        </EditorDialog>
      )}
    </section>
  );
}
