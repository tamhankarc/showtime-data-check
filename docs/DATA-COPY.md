# One-time Admin reference copy

The `app.seed_admin` command separates source export from target import. Review the counts and hash manifest between stages. Do not commit or publish the reference JSON. Use the new app's ignored, untracked `private/` directory or a private directory outside Git; on Windows, select a directory with a private user ACL. Existing export files are never overwritten.

The exporter reads explicit columns from reference tables in a repeatable-read, read-only PostgreSQL transaction and rolls it back afterward. It never selects credentials, passwords, users, logos, showtimes, saved data checks, client data, analytics, or provider response dumps. Console output contains section counts and SHA-256 hashes only; database errors suppress connection details.

The seed includes every configured IS country/provider connection and Brazil's Ingresso, Cinemark, and Velox connections, including disabled configuration. BO provider data is excluded. All provider movie records are copied, including unlinked and unavailable entries. Confirmed canonical title IDs create country-specific movie groups; unlinked provider IDs each get their own group, even when their names are identical. Confirmed groups are marked to preserve the existing decision. No manual Title creation or country-link workflow is introduced.

Canonical theater IDs, addresses, coordinates, timezones, websites, chain names, provider theater IDs, confirmed canonical assignments, and review states are retained. Pending Admin assignments can contain automatic guesses: they are imported as unassigned **Needs Review** records with their observed details; all canonical theaters remain copied. Format mappings retain published labels, language codes, and dubbed/subtitled/luxury flags. Title-specific format overrides are copied when both the base mapping and its confirmed country-specific movie group are seeded, including their active/inactive state. Chain names are currently stored on theaters; independent chain records are not represented by the current app model. Original movie names and native parent IDs are absent from the existing Admin `provider_title` table and remain empty until a provider catalog refresh supplies them.

## Export

Run from `backend/` with the app's environment installed. Create the ignored `private/` directory first. Choose either source connection method:

```powershell
# Set ADMIN_DATABASE_URL privately in the process environment; never put a DSN in command arguments.
python -m app.seed_admin export --out ../private/admin-reference.json
```

Or read the existing local Docker PostgreSQL service without exposing a database port:

```powershell
python -m app.seed_admin export --out ../private/admin-reference.json --docker-compose ../../application/local/compose.yaml
```

The Docker fallback uses `docker compose exec -T postgres psql` with the existing local database/user defaults (`title_ticketing` / `title_local`). `--docker-database` and `--docker-user` can override these non-secret names. It does not start containers, change the source database, read passwords, or publish ports.

## Review and import

Review the private bundle and manifest locally. SHA-256 hashes detect accidental changes; they are not a digital signature. Initialize the new app's schema using its normal setup, and verify `DATABASE_URL` points to the new app database. Import does not read Admin or copy authentication records.

```powershell
python -m app.seed_admin import --file ../private/admin-reference.json --confirm-one-time
```

The import validates fields, scope, references, counts, and hashes before writing. It requires empty reference tables and creates a one-time marker in the same transaction. A failed import rolls back all copied rows. Existing app decisions are never overwritten. PostgreSQL identity sequences advance beyond copied IDs. Seeded catalogs show `seeded_not_refreshed`; their data does not claim a successful live refresh.

Afterward the app is independent: there is no ongoing Admin sync. Keep provider credentials and the two shared login credentials in the new app's private environment, managed separately from this reference copy. Remove the private JSON when its review/transfer purpose is complete, according to your local retention needs.
