# Provider Data Check

Independent provider catalog/showtime inspection for International Showtimes countries and Brazil (Ingresso, Cinemark, Velox). Next.js frontend for Vercel; FastAPI API/worker and PostgreSQL for Railway.

Two shared logins are configured outside the source code:

- Administrator: full reference-data management, reviews, movie ID groups and formats, plus all checking functions.
- Viewer: refresh provider catalogs, browse Provider Movies, load/filter fresh showtimes/theaters, and export XLSX. Reference writes are rejected by the backend.

## Start locally on Windows

Docker Desktop, Python 3.12+ and Node.js 22.13+ are required. Keep the current Admin site separate; this project uses ports 3001, 8001 and 5434.

1. Open PowerShell in `D:\projects\ticketingadmin\showtime-data-check`.
2. Start the separate database: `docker compose up -d postgres`.
3. In a second PowerShell window, enter the `backend` folder and run `.\.venv\Scripts\python.exe -m app.local_run`.
4. In another window, enter the `frontend` folder and run `npm.cmd run dev -- --hostname 127.0.0.1 --port 3001`.
5. Open **http://localhost:3001**. The exact hostname matters for request-origin protection.
6. Read the generated, ignored `private/local-access.txt` for both local logins. Do not commit or send this file.

For a fresh checkout, create the isolated Python environment first (`python -m venv backend/.venv`, then install `backend/requirements.txt`), run `npm.cmd ci` in `frontend`, and run `python -m app.local_setup` from `backend`. The optional `--reuse-admin-credentials` switch reads authorized local provider credentials; it never changes Admin. Initialize the new schema by starting the backend, then follow [the one-time copy guide](docs/DATA-COPY.md). Do not seed a database that already contains reference data.

Stop the frontend/backend with Ctrl+C in their windows. `docker compose stop postgres` stops only this project's database and keeps its volume. Never use `down -v` unless intentionally deleting this new project's database.

## Behavior

- Pages never fetch provider sessions on entry. Select Country and Movie, then **Load data**.
- Brazil supports multiple provider movie IDs, including English/localized/variant names. Options display `Name (provider ID)` and provider identity.
- Provider Movies reads the stored catalog; catalog refresh is a separate explicit action.
- Velox catalog discovery combines its release window with theater schedules for the next 120 days; it does not retain those schedule bodies.
- Confirmed theater assignments and manual movie groups survive refresh. New records remain Needs Review; provider names and IDs are preserved.
- Generic a-z/1-9 PNGs only; an initial tile handles unavailable generic assets.
- Published-format mappings and movie-specific overrides are copied. Unmapped values display the provider raw format. Language, dubbed/subtitled and VIP distinctions are preserved.
- No usable link means neither booking nor movie-page/theater fallback exists.
- Provider results exist in RAM only, for at most 15 minutes from each source's fetch completion. Filtering/view switching does not extend them. Expiry/reference changes require explicit Reload and disable exports.
- XLSX files are generated entirely in memory on demand, including workbook assembly; no temporary export files are written or retained on the server. Exported local files are controlled by the user.
- There is no automatic import schedule, JSON publication, BO support, or ongoing Admin synchronization.

## Deployment and verification

See [Railway and Vercel setup](docs/DEPLOYMENT.md), [approved scope](planning/SCOPE.md), and [data-copy safeguards](docs/DATA-COPY.md).

Backend: `backend/.venv/Scripts/python.exe -m pytest backend/tests -q` from the repository root with `PYTHONPATH=backend`, or run `.venv/Scripts/python.exe -m pytest tests -q` inside `backend`.

Frontend inside `frontend`: `npm.cmd test`, `npm.cmd run typecheck`, `npm.cmd run build`.

`scripts/verify_local.py` tests the local HTTP/proxy/permission flow without printing credentials. `--refresh` explicitly contacts all enabled authorized provider catalogs; `--live-check` performs a live AU check, generates an XLSX in memory, and then discards the check. These flags may consume provider/API usage.
