# Provider Data Check — Scope for review

Date: 24 October 2026 · Asia/Calcutta

Target repository: https://github.com/tamhankarc/showtime-data-check

Status: approved for local implementation; hosting setup follows verification.

## Product and independence

Build a separate Provider Data Check app for inspecting current provider catalogs, upcoming screenings, and theaters showing a selected movie. Initial coverage is existing IS-supported countries/providers, plus Brazil's Ingresso, Cinemark, and Velox. BO is excluded.

Use Vercel for the web app and Railway PostgreSQL for durable catalog/configuration data. Use two shared logins: full-access administrator and catalog-refresh/read/export viewer. Enforce permissions server-side; no user-management screens or individual user accounts. Provider work runs in a persistent Railway backend, not long-running Vercel requests. Hosting setup and production credentials must be verified before deployment; no pricing commitment is made.

One-time copy relevant current country/provider settings, canonical theaters/chains, Brazil provider assignments, confirmed movie groupings, and format mappings. Preserve manual decisions. Thereafter the app operates independently, without ongoing Admin synchronization or changes to the current Admin app. Reuse existing authorized provider credentials and the signed AWS Cinemark relay server-side only.

## Catalog and management

Import **all** provider movies and automatically create catalog entries. Do not require manual Title creation or Admin-style country linking. Successful refreshes update availability; failed refreshes preserve the last good catalog. Home shows last successful movie-catalog refresh, current status, and counts per country/provider, with manual refresh controls.

For Brazil, group imported IDs across providers and variants, including Velox parents/children. Seed existing confirmed mappings and use trustworthy provider-native relationships. Allow manual combine, split, and reassignment of imported IDs. Never infer automatic merges from names alone.

Management covers country/provider enablement, imported movies and Brazil groups, canonical theaters/chains, Brazil provider assignments, and format mappings. Newly discovered provider theaters are saved as **Needs Review**; do not auto-merge them. Manual review and assignments remain authoritative across refreshes. Use generic theater logos containing the first Latin letter or digit only (`a-z`, `0-9`); no custom or chain logos.

## Result workflow

Showtimes and Theaters by Movie initially display empty states. Require Country and Movie, then explicit **Load data**. Selecting Brazil reveals a searchable Provider dropdown, default **All**, and a searchable multi-select movie selector. Each movie option displays its provider ID in circular brackets: `Movie name (1234)`, with provider identified separately. Brazil users can select several English/localized/variant IDs for one logical movie without requiring a permanent grouping. Other countries use one provider movie selection. Fetch only the chosen provider(s). Entry into a page must never trigger a provider fetch.

Add a **Provider Movies** page, accessible to both logins. Select a country to browse/search available provider movie names, provider IDs, provider name, and original/localized name when supplied. Brazil can also filter by provider, default All. This page reads the stored catalog; it does not fetch provider sessions.

Every explicit Load makes fresh provider requests. Filters, pagination, and switching result views may reuse a matching, current, unexpired snapshot. Support cancellation, progress, and bounded concurrency. Include screenings from today onward using each provider/theater's local time, not the browser's date alone.

Movie-group, theater-assignment, theater, or format-mapping edits invalidate affected snapshots immediately. Mark affected results stale, disable export, and require an explicit **Reload**. Such edits must not trigger automatic fetching.

Show mapped published formats where available, otherwise the original raw provider format. Preserve raw format strings and audio/subtitle/dubbing languages for inspection. Provide a format-mapping page similar to the existing Admin and seed its current settings.

Brazil results combine identical screenings at a canonical theater and expose multiple booking links. Preserve distinctions in format, audio, subtitles, dubbing, and VIP; provider filtering must retain correct counts and links.

Showtimes metrics are total distinct merged screenings, distinct theaters, **No usable link**, and IMAX screenings. Count No usable link only when a screening has neither a booking URL nor a usable movie-page/theater fallback. Do not add a second missing-link metric.

Theaters by Movie follows the existing Admin columns: Movie; Theater with ID, chain, and address; Country; upcoming showtime count and first/last times; Formats; Providers. A compact usable-booking-link indicator may supplement these columns.

If any requested provider fails, clearly mark results **Incomplete**, show each provider's outcome, and offer per-provider Retry. An incomplete result must never appear fully successful. Show per-provider fetch completion times so mixed-age results are evident.

## Data lifetime and exports

Showtime/session snapshots may use in-memory, cache, or other temporary storage with a hard maximum lifetime of **15 minutes from fetch completion**. Activity never extends expiry. After expiry, require explicit Reload and disable export. On retry, each provider slice keeps its own completion-based deadline; retrying one provider must not renew another slice. Discard parsing scratch data as soon as extraction completes. No PostgreSQL showtime rows, durable history, retained provider raw dumps, or retained exports are permitted. Keep showtime bodies out of logs and backups.

Both result pages provide XLSX export of the currently filtered results, disabled until valid results are loaded. Include the country/movie/provider selection, provider fetch times, and completeness/partial-failure status. Generate exports on demand with no retained server copy. Durable storage is limited to catalogs, identities, mappings, assignments, and operational metadata without showtime bodies.

## Shared interaction rules

All dropdowns are searchable: rank exact and prefix matches before substring matches; otherwise use alphabetical ordering with natural numeric sorting. Include an exact-match checkbox for text searches where applicable. Keep selection, loading, cancelled, empty, expired, partial-failure, and successful states explicit.

## Exclusions

No BO, analytics, JSON publication, historical showtimes, permanent showtime/raw-response/export retention, individually managed users, manual Title creation, or ongoing Admin sync. The two shared permission levels are the only roles in this version.

## Acceptance checklist

- [ ] Independent app and one-time seed preserve confirmed/manual mappings; all provider movies import automatically.
- [ ] Home exposes catalog freshness, status, counts, and manual refresh; failed refresh retains last good data.
- [ ] Both result pages start empty, require Country/Movie and Load, and support Brazil Provider = All or one provider.
- [ ] Loads are fresh; view/filter reuse is selection-matched and bounded by fixed per-provider 15-minute deadlines. Mapping edits invalidate affected results; expiry/invalidation disables export and requires explicit Reload.
- [ ] Local dates, screening deduplication, distinct metrics, link fallback, raw formats, and language/VIP distinctions are correct.
- [ ] Partial failures remain visibly incomplete; progress, cancellation, bounded concurrency, and provider retries work.
- [ ] Both filtered XLSX exports include selection, timing, and completeness; no permanent exports or showtime bodies persist.
- [ ] Management, Needs Review theaters, generic logos, searchable ordering, and seeded format mappings meet these rules.
- [ ] Administrator can manage references; viewer can refresh catalogs, run checks, browse Provider Movies, and export but cannot mutate reference data.
- [ ] Movie options display `Name (provider ID)`; Brazil supports multiple provider movie selections, including localized variants.
