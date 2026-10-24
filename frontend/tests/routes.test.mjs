import test from "node:test";
import assert from "node:assert/strict";
import {
  apiUrl,
  adminPages,
  pagePaths,
  regularPages,
  sectionAllowed,
  sectionForPath,
  sectionSlugs,
} from "../src/lib/routes.ts";

test("all nine pages have unique canonical normal URLs ending in a slash", () => {
  assert.equal(Object.keys(pagePaths).length, 9);
  assert.equal(new Set(Object.values(pagePaths)).size, 9);
  for (const [section, path] of Object.entries(pagePaths)) {
    assert(path.startsWith("/") && path.endsWith("/"));
    assert(!path.includes("#") && !path.includes("?"));
    assert.equal(sectionForPath(path), section);
    if (path !== "/") assert.equal(sectionForPath(path.slice(0, -1)), section);
  }
  assert.deepEqual(
    sectionSlugs.sort(),
    [
      "countries",
      "formats",
      "movies",
      "provider-movies",
      "provider-theaters",
      "showtimes",
      "theaters",
      "theaters-by-movie",
    ].sort(),
  );
});
test("unknown, external and hash-like sections are not interpreted as application pages", () => {
  for (const path of [
    null,
    "",
    "/unknown/",
    "/showtimes/extra/",
    "/showtimes//",
    "/#showtimes",
    "/showtimes?next=/countries/",
    "https://example.com/showtimes/",
    "//showtimes/",
  ])
    assert.equal(sectionForPath(path), null);
});
test("viewer route access excludes every administration section", () => {
  for (const section of regularPages) {
    assert.equal(sectionAllowed("viewer", section), true);
    assert.equal(sectionAllowed("admin", section), true);
  }
  for (const section of adminPages) {
    assert.equal(sectionAllowed("viewer", section), false);
    assert.equal(sectionAllowed("admin", section), true);
  }
  assert.equal(sectionAllowed("viewer", null), false);
  assert.equal(sectionAllowed("admin", null), false);
});
test("API URLs are canonical without moving query parameters after the slash", () => {
  assert.equal(apiUrl("login"), "/api/login/");
  assert.equal(apiUrl("session/"), "/api/session/");
  assert.equal(
    apiUrl("provider-movies?country=BR&q=a%2Fb"),
    "/api/provider-movies/?country=BR&q=a%2Fb",
  );
  assert.equal(
    apiUrl("checks/123/export?view=theaters&page=2"),
    "/api/checks/123/export/?view=theaters&page=2",
  );
});
