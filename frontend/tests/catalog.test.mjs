import test from "node:test";
import assert from "node:assert/strict";
import {
  catalogWindowValue,
  providerMoviesEndpoint,
} from "../src/lib/catalog.ts";
import {
  preserveSelectedChoices,
  providerMovieChoice,
} from "../src/lib/search.ts";
import { referenceListQuery } from "../src/lib/reference.ts";

test("IS catalog windows default to unlimited and accept only whole discovery-day ranges", () => {
  for (const value of [null, undefined, "", "  ", 0, "0"])
    assert.equal(catalogWindowValue(value), null);
  assert.equal(catalogWindowValue("1"), 1);
  assert.equal(catalogWindowValue(3650), 3650);
  for (const value of [
    -1,
    "-2",
    3651,
    "90.5",
    NaN,
    Infinity,
    "tomorrow",
    false,
    {},
  ])
    assert.throws(() => catalogWindowValue(value));
});

test("movie catalog endpoints include archived records only by explicit opt-in and never filter release age", () => {
  const regular = new URL(
    providerMoviesEndpoint("UK", "all"),
    "https://example.test/",
  );
  assert.equal(regular.searchParams.get("country"), "UK");
  assert.equal(regular.searchParams.has("include_inactive"), false);
  const archived = new URL(
    providerMoviesEndpoint("BR", "INGRESSO", true),
    "https://example.test/",
  );
  assert.equal(archived.searchParams.get("include_inactive"), "true");
  assert.equal(archived.searchParams.get("provider"), "INGRESSO");
  for (const key of [
    "release_date_from",
    "release_date_to",
    "time_from",
    "time_to",
    "catalog_window_days",
  ])
    assert.equal(archived.searchParams.has(key), false);
});

test("older active films and archived IDs retain their original selectable identity", () => {
  const oldFilm = providerMovieChoice({
    id: 101,
    name: "A 1950 Classic",
    provider_code: "IS",
    external_id: "classic-1",
    active: true,
    release_date: "1950-01-01",
  });
  assert.equal(oldFilm.value, "101");
  assert.match(oldFilm.detail, /Available/);
  const archived = providerMovieChoice({
    id: 102,
    name: "Da Magia",
    provider_code: "INGRESSO",
    external_id: "32130",
    url_key: "da-magia",
    group_name: "Practical Magic 2",
    active: false,
  });
  assert.equal(archived.value, "102");
  assert.match(archived.detail, /Archived/);
  assert.match(archived.detail, /Practical Magic 2/);
  assert.equal(archived.label, "Da Magia (da-magia)");
});

test("changing catalog/archive search results preserves chosen movie IDs and labels without restoring unselected old options", () => {
  const active = {
    value: "11",
    label: "An active movie (88)",
    detail: "IS · Available",
  };
  const archived = {
    value: "22",
    label: "An archived movie (old-slug)",
    detail: "INGRESSO · Archived",
  };
  const unselected = { value: "33", label: "Not selected (33)" };
  const cache = new Map(
    [active, archived, unselected].map((choice) => [choice.value, choice]),
  );
  const merged = preserveSelectedChoices([active], ["11", "22"], cache);
  assert.deepEqual(
    merged.map((choice) => choice.value),
    ["11", "22"],
  );
  assert.equal(merged[1].label, archived.label);
  assert.equal(
    merged.some((choice) => choice.value === "33"),
    false,
  );
  assert.deepEqual(preserveSelectedChoices([], ["22"], new Map(), [archived]), [
    archived,
  ]);
});

test("manual movie groups have explicit archive visibility independent of theater visibility", () => {
  const filters = {
    resource: "movie-groups",
    q: "",
    exact: false,
    page: 1,
    country: "BR",
    provider: "",
    status: "",
    showInactiveTheaters: false,
  };
  assert.equal(referenceListQuery(filters).has("include_inactive"), false);
  assert.equal(
    referenceListQuery({ ...filters, includeArchivedMovies: true }).get(
      "include_inactive",
    ),
    "true",
  );
  assert.equal(
    referenceListQuery({
      ...filters,
      resource: "countries",
      includeArchivedMovies: true,
    }).has("include_inactive"),
    false,
  );
});
