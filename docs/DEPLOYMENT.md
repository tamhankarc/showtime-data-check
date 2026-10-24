# Railway + Vercel setup

Prepared 24 October 2026. This guide describes this repository, not a deployment already performed.

## Access

GitHub is connected in the current work session and the blank `tamhankarc/showtime-data-check` repository is accessible. Do not send GitHub passwords, personal access tokens, provider passwords, or database passwords in chat.

For Railway, sign in in your own browser. If command-line assistance is wanted, install the official CLI with `npm.cmd install -g @railway/cli`, then run `railway login` and complete the browser flow yourself. This makes the authenticated local CLI available without posting an account token. Tell the developer which project/environment to use. [Official CLI authentication guide](https://docs.railway.com/cli).

## 1. Create the Railway project and database

1. Open the [Railway dashboard](https://railway.com/dashboard).
2. Choose **New Project → Empty Project**, named **showtime-data-check**.
3. On the project canvas choose **+ New → Database → PostgreSQL**.
4. Keep the database's normal private connection. Do not add a public database endpoint merely for Vercel: only the API service connects to PostgreSQL.
5. Keep the persistent database volume, enable an appropriate backup schedule, and set a spending/usage alert in your account. Confirm plan charges before approving a paid plan.

Railway documents adding PostgreSQL and using its connection variables between services in the same project. [PostgreSQL setup and private connections](https://docs.railway.com/databases/postgresql).

## 2. Add the Railway API/worker service

After the tested source is pushed to GitHub:

1. In the same project, **+ New → GitHub Repo** and select **tamhankarc/showtime-data-check**. Authorize only the needed repository if prompted.
2. Name the service **provider-data-check-api**.
3. Set **Root Directory** to `/backend`. Use the Dockerfile in that directory. If a configuration-file path must be selected, use `/backend/railway.toml`.
4. Keep **one replica** and **one Uvicorn worker**, as already declared by this app. Multiple processes would not share the temporary in-memory results.
5. Set the variables below before expecting a healthy production start. Then deploy.
6. Check `/health`, then use **Settings → Networking → Generate Domain** for the **API service only**. Vercel needs its HTTPS URL. Other API routes require both the server-side proxy secret and a valid login; `/health` reveals no reference data.

Railway supports GitHub-backed FastAPI deployment, Dockerfiles, health checks and a generated public service domain. [Official FastAPI guide](https://docs.railway.com/guides/fastapi).

### API variables

| Variable | Value |
| --- | --- |
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | Reference the private PostgreSQL service URL, e.g. `${{Postgres.DATABASE_URL}}`; choose the actual database service in Railway's variable-reference picker. |
| `APP_ORIGIN` | Exact final Vercel HTTPS origin, no trailing path, e.g. `https://your-project.vercel.app` |
| `PROXY_SECRET` | Strong random value, at least 32 characters. Must match the Vercel server variable. |
| `ADMIN_USERNAME` | Full-access shared username |
| `ADMIN_PASSWORD_HASH` | PBKDF2 hash, not the plain password |
| `VIEWER_USERNAME` | Different refresh/view shared username |
| `VIEWER_PASSWORD_HASH` | PBKDF2 hash for the second login |
| `IS_API_KEY` | Existing authorized IS key |
| `VELOX_USERNAME`, `VELOX_PASSWORD` | Existing authorized Velox credentials |
| `INGRESSO_PARTNERSHIPS` | `warner,sony`, or the authorized partnerships in use |
| `CINEMARK_RELAY_URL`, `CINEMARK_RELAY_SECRET` | Existing approved signed AWS relay configuration |

Run `.venv/Scripts/python.exe -m app.hash_password` inside `backend` to choose each password interactively. It asks for a repeat and prints only the required hash. Use at least 12 characters. The local setup generated independent local credentials; do not publish `private/backend.env` or the local passwords to GitHub.

Use the Railway variable editor or an authenticated secret-input workflow. Do not put secrets into shell command arguments, source files, issue comments, or screenshots. Changing a password hash invalidates sessions for that login after deployment.

## 3. Create the Vercel frontend

1. Sign in to [Vercel](https://vercel.com/), choose **Add New → Project**, and import **tamhankarc/showtime-data-check**.
2. Choose **Next.js** and set the **Root Directory** to `frontend`.
3. Keep the repository build/install settings (`npm ci`, `npm run build`). Choose a supported Node.js version satisfying the package engine (22.13+).
4. Add server-only variables:
   - `BACKEND_URL=https://YOUR-RAILWAY-API-DOMAIN/api`
   - `PROXY_SECRET` identical to the API value.
5. Do **not** prefix either variable with `NEXT_PUBLIC_`. No database/provider credential belongs in Vercel or browser code.
6. Deploy, copy the stable production origin, and set that exact value in Railway `APP_ORIGIN`. Redeploy the API if changed.
7. Do not point arbitrary Vercel preview deployments at production credentials/data. Use an isolated environment, or leave previews unconfigured.

Vercel supports GitHub project imports and per-environment server variables; variable changes apply to subsequent deployments. [GitHub integration](https://vercel.com/docs/git/vercel-for-github), [environment variables](https://vercel.com/docs/environment-variables).

## 4. Import the one-time reference copy and verify

The local copy is in ignored `private/admin-reference.json`; it is not in GitHub and contains no showtimes, credentials or user passwords. It has already been imported into the independent local PostgreSQL instance. The Railway database needs its own **single** import.

After Railway authentication, perform the reviewed seed through an authenticated private/remote execution workflow. Do not make the source Admin database public, paste the bundle into a public repository, or overwrite an initialized target. See [DATA-COPY.md](DATA-COPY.md). The importer rejects a previously seeded or nonempty reference database and validates the manifest hash. A production import is a separate operation from local import.

Verify both shared logins, viewer write-denial, Provider Movies browsing/search, catalog refresh, BR multi-ID selection, fresh check progress, multiple booking links, format fallback, filtered XLSX exports, expiry/Reload, and logout. Check each provider outcome; partial results must remain visibly incomplete. Provider credentials must never appear in browser traffic or logs.

## Operational limits and cost boundaries

The API and database remain running services and incur their applicable Railway plan/resource charges; a 15-minute result lifetime does not make hosting free. The existing AWS Cinemark relay still incurs its normal request/transfer usage. No new S3 storage, showtime database history, permanent raw feeds, or scheduled automatic provider traffic is introduced.

Start with a single API instance with enough memory for bounded Cinemark/Velox parsing (2 GB is a conservative initial ceiling to validate, not a promised fixed bill). Monitor real usage and tune after measurements. Provider concurrency is bounded at two; result memory and pages are bounded. Do not enable extra replicas without redesigning transient result sharing.

Restarting the API intentionally discards in-memory checks. Persisted job metadata then reports interrupted work as failed, and users explicitly reload. Database backups contain reference data/settings and operational metadata, never showtime bodies. Raw provider responses and secrets must not be added to logs.
