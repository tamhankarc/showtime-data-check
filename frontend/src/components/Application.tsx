"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  adminPages,
  pagePaths,
  regularPages,
  sectionAllowed,
  sectionForPath,
  type Page,
} from "@/lib/routes";
import { api, ApiError, localTimestamp, send } from "@/lib/api";
import type { Bootstrap, Job, Session } from "@/lib/types";
import { ErrorNotice, Status } from "./Shared";
import { Results } from "./Results";
import { Management } from "./Management";
import { ProviderMovies } from "./ProviderMovies";

const pageLabels: Record<Page, string> = {
  home: "Home",
  showtimes: "Showtimes",
  theaters: "Theaters by movie",
  catalog: "Provider movies",
  countries: "Countries & providers",
  movies: "Movies & Brazil groups",
  directory: "Theater directory",
  assignments: "Provider theaters",
  formats: "Format mappings",
};
const navIcons: Record<Page, string> = {
  home: "⌂",
  showtimes: "◷",
  theaters: "▤",
  catalog: "▣",
  countries: "◎",
  movies: "▣",
  directory: "⌖",
  assignments: "⇄",
  formats: "≋",
};
export function Application({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const page = sectionForPath(usePathname());
  const knownRoute = page !== null;
  const [session, setSession] = useState<Session | null>(null);
  const [data, setData] = useState<Bootstrap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const refresh = useCallback(async () => {
    const next = await api<Bootstrap>("bootstrap");
    setData(next);
  }, []);
  const initialize = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const identity = await api<Session>("session");
      setSession(identity);
      await refresh();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401))
        setError((e as Error).message);
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, [refresh]);
  useEffect(() => {
    if (knownRoute) void initialize();
  }, [initialize, knownRoute]);
  useEffect(() => {
    if (session && page && !sectionAllowed(session.role, page)) {
      setError(
        "This section requires full access. You have been returned to Home.",
      );
      router.replace(pagePaths.home);
    }
  }, [session, page, router]);
  useEffect(() => {
    const expired = () => {
      setSession(null);
      setData(null);
      setPassword("");
      setError("Your session has expired. Sign in again.");
    };
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  async function login(e: React.FormEvent) {
    e.preventDefault();
    setSigningIn(true);
    setError("");
    try {
      await send("login", { username, password });
      setPassword("");
      await initialize();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSigningIn(false);
    }
  }
  async function logout() {
    setError("");
    try {
      await send("logout", {});
      setSession(null);
      setData(null);
      setPassword("");
      setMenuOpen(false);
      router.replace(pagePaths.home);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (!page) return <>{children}</>;
  if (loading || (session && data && !sectionAllowed(session.role, page)))
    return (
      <div className="startup">
        <span className="spinner" />
        <p>Opening Provider Data Check…</p>
      </div>
    );
  if (!session || !data)
    return (
      <main className="login-page">
        <form className="login-card" onSubmit={login}>
          <span className="brand-mark">SD</span>
          <div>
            <p className="eyebrow">Private provider checking</p>
            <h1>Provider Data Check</h1>
            <p className="muted">
              Sign in to refresh catalogs and inspect current showtimes.
            </p>
          </div>
          {error && <ErrorNotice message={error} />}
          <div className="field">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              maxLength={128}
            />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              maxLength={512}
            />
          </div>
          <button className="button primary" type="submit" disabled={signingIn}>
            {signingIn ? "Signing in…" : "Sign in"}
          </button>
          <small>
            Access is limited to the administrator and data-viewing accounts.
          </small>
        </form>
      </main>
    );
  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-mark">SD</span>
          <div>
            <strong>Showtime Data</strong>
            <small>Provider checking</small>
          </div>
        </div>
        <nav aria-label="Main navigation">
          {regularPages.map((item) => (
            <Link
              key={item}
              href={pagePaths[item]}
              prefetch={false}
              className={page === item ? "active" : ""}
              aria-current={page === item ? "page" : undefined}
              onClick={() => {
                setMenuOpen(false);
              }}
            >
              <span aria-hidden="true">{navIcons[item]}</span>
              {pageLabels[item]}
            </Link>
          ))}
          {session.role === "admin" && (
            <>
              <div className="nav-caption">Manage reference data</div>
              {adminPages.map((item) => (
                <Link
                  key={item}
                  href={pagePaths[item]}
                  prefetch={false}
                  className={page === item ? "active" : ""}
                  aria-current={page === item ? "page" : undefined}
                  onClick={() => {
                    setMenuOpen(false);
                  }}
                >
                  <span aria-hidden="true">{navIcons[item]}</span>
                  {pageLabels[item]}
                </Link>
              ))}
            </>
          )}
        </nav>
        <div className="sidebar-foot">
          Independent reference data
          <br />
          Showtimes expire within 15 minutes
          <br />
          Generic theater images only
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="button-row">
            <button
              className="mobile-menu button small"
              onClick={() => setMenuOpen(!menuOpen)}
              aria-expanded={menuOpen}
              aria-label="Toggle navigation"
            >
              ☰
            </button>
            <div>
              <strong>{pageLabels[page]}</strong>
              <small>Provider Data Check</small>
            </div>
          </div>
          <div className="button-row">
            <span className="account">
              <strong>{session.username}</strong>
              <small>
                {session.role === "admin"
                  ? "Full access"
                  : "Catalog refresh & viewing"}
              </small>
            </span>
            <button className="button" type="button" onClick={logout}>
              Sign out
            </button>
          </div>
        </header>
        <main className="content">
          {error && <ErrorNotice message={error} />}
          {page === "home" ? (
            <Home data={data} onRefresh={refresh} />
          ) : page === "catalog" ? (
            <ProviderMovies data={data} />
          ) : page === "showtimes" || page === "theaters" ? (
            <Results view={page} data={data} />
          ) : session.role === "admin" ? (
            <Management page={page} data={data} onChange={refresh} />
          ) : null}
        </main>
        <footer className="app-footer">
          Catalogs are stored independently. Showtime results are temporary, not
          a historical archive.
        </footer>
      </div>
    </div>
  );
}

function Home({
  data,
  onRefresh,
}: {
  data: Bootstrap;
  onRefresh: () => Promise<void>;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!job || !["queued", "processing"].includes(job.status)) return;
    const timer = setTimeout(async () => {
      try {
        const next = await api<Job>(`jobs/${job.id}`);
        setJob(next);
        if (!["queued", "processing"].includes(next.status)) await onRefresh();
      } catch (e) {
        setError((e as Error).message);
        setBusy(false);
      }
    }, 2000);
    return () => clearTimeout(timer);
  }, [job, onRefresh]);
  async function refresh(ids?: number[]) {
    setError("");
    setBusy(true);
    try {
      const result = await send<{ job_id: string }>(
        "catalogs/refresh",
        ids ? { connection_ids: ids } : {},
      );
      setJob({
        id: result.job_id,
        status: "queued",
        message: "Catalog refresh queued.",
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const working =
    busy || (!!job && ["queued", "processing"].includes(job.status));
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Provider ingestion</p>
          <h1>Catalog overview</h1>
          <p>
            Refresh available movie names. Load showtimes separately when you
            need them.
          </p>
        </div>
        <button
          className="button primary"
          disabled={working || !data.connections.some((c) => c.enabled)}
          onClick={() => refresh()}
        >
          ↻ Refresh all catalogs
        </button>
      </div>
      {error && (
        <ErrorNotice
          message={error}
          retry={() => {
            setError("");
            if (job && ["queued", "processing"].includes(job.status))
              setJob({ ...job });
            else void onRefresh().catch((e) => setError(e.message));
          }}
        />
      )}
      {job && (
        <div
          className={`notice ${job.status === "partial" || job.status === "failed" ? "warning" : ""}`}
          role="status"
        >
          <div>
            <Status value={job.status} />
            <p>{job.message || "Refreshing provider catalogs…"}</p>
            {job.progress !== undefined && (
              <progress value={job.progress} max={100} />
            )}
            {job.provider_outcomes?.map((outcome) => (
              <small
                key={
                  outcome.connection_id ||
                  `${outcome.country_code}-${outcome.provider_code}`
                }
              >
                {outcome.country_code ? `${outcome.country_code} · ` : ""}
                {outcome.provider_code}: {outcome.status} {outcome.message}
              </small>
            ))}
          </div>
        </div>
      )}
      <div className="summary-row">
        <div>
          <span>Countries</span>
          <strong>
            {data.countries.filter((c) => c.enabled !== false).length}
          </strong>
        </div>
        <div>
          <span>Enabled connections</span>
          <strong>{data.connections.filter((c) => c.enabled).length}</strong>
        </div>
        <div>
          <span>Provider movie IDs</span>
          <strong>
            {data.connections
              .reduce((sum, c) => sum + (c.movie_count || 0), 0)
              .toLocaleString()}
          </strong>
        </div>
      </div>
      <section className="panel">
        <div className="panel-head">
          <h2>Available provider catalogs</h2>
          <small>Refresh times are displayed in your device timezone.</small>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Country</th>
                <th>Provider</th>
                <th>Last successful refresh</th>
                <th>Movies</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {data.connections.map((connection) => (
                <tr key={connection.id}>
                  <td>
                    <strong>
                      {data.countries.find(
                        (c) => c.code === connection.country_code,
                      )?.name || connection.country_code}
                    </strong>
                    <small>{connection.country_code}</small>
                  </td>
                  <td>{connection.provider_code}</td>
                  <td>{localTimestamp(connection.last_success_at)}</td>
                  <td>{connection.movie_count?.toLocaleString() ?? "—"}</td>
                  <td>
                    <Status
                      value={
                        !connection.enabled
                          ? "disabled"
                          : connection.last_status
                      }
                    />
                  </td>
                  <td>
                    <button
                      className="button small"
                      disabled={!connection.enabled || working}
                      onClick={() => refresh([connection.id])}
                    >
                      Refresh catalog
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data.connections.length && (
            <div className="empty">
              <h2>No connections configured</h2>
              <p>
                The administrator can add country/provider connections or import
                the one-time reference copy.
              </p>
            </div>
          )}
        </div>
      </section>
      <div className="notice">
        <div>
          <strong>Fresh results, on request</strong>
          <p>
            Open Showtimes or Theaters by movie, choose a country and movie, and
            click Load data. Merely opening a page does not call a provider.
            Temporary results expire no later than 15 minutes after fetching.
          </p>
        </div>
      </div>
    </>
  );
}
