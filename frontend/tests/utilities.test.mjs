import test from "node:test";
import assert from "node:assert/strict";
import {
  rankChoices,
  genericInitial,
  safeExternalUrl,
  providerMovieChoice,
  recordSearchValues,
} from "../src/lib/search.ts";
import { allowedPath } from "../src/lib/proxy.ts";
import nextConfig from "../next.config.ts";
test("search orders exact, prefix, word, substring and secondary IDs", () => {
  const rows = [
    { value: "a", label: "IMAX PLF" },
    { value: "b", label: "PLF 3D" },
    { value: "c", label: "PLF" },
    { value: "d", label: "Special", detail: "PLF" },
    { value: "e", label: "SimplePLF" },
  ];
  assert.deepEqual(
    rankChoices(rows, "plf").map((x) => x.value),
    ["c", "b", "a", "e", "d"],
  );
});
test("search is accent insensitive, numeric alphabetical and matches localized ID details", () => {
  assert.equal(
    rankChoices(
      [
        { value: "1", label: "Ciné 12" },
        { value: "2", label: "Cine 2" },
      ],
      "cine",
    )[0].value,
    "2",
  );
  assert.equal(
    rankChoices(
      [
        {
          value: "1",
          label: "Practical Magic",
          detail: "Magia Prática · Cinemark 9308",
        },
      ],
      "9308",
    ).length,
    1,
  );
});
test("only safe external web links allowed", () => {
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("https://user:pass@test.com"), null);
  assert.equal(
    safeExternalUrl("https://example.com/a"),
    "https://example.com/a",
  );
});
test("generic theater logo uses first normalized letter/digit", () => {
  assert.equal(genericInitial("  * Éden"), "E");
  assert.equal(genericInitial("123 Cinema"), "1");
  assert.equal(genericInitial("😀"), "T");
});
test("proxy only forwards supported routes", () => {
  assert(allowedPath(["checks", "123", "export"]));
  assert(allowedPath(["countries", "BR"]));
  assert(!allowedPath(["..", "secret"]));
  assert(!allowedPath(["http:", "evil.com"]));
  assert(!allowedPath(["checks", "a%2Fb"]));
  assert(!allowedPath(["settings", "credentials"]));
});
test("movie options always include provider ID in parentheses and localized names stay searchable", () => {
  const choice = providerMovieChoice({
    id: 11,
    name: "Magia Prática 2",
    external_id: "9308",
    provider_code: "CINEMARK",
    original_name: "Practical Magic 2",
  });
  assert.equal(choice.label, "Magia Prática 2 (9308)");
  assert(choice.detail.includes("CINEMARK"));
  assert.equal(rankChoices([choice], "Practical Magic").length, 1);
});
test("nested provider records are individually searchable for exact matches", () => {
  assert(
    recordSearchValues({
      name: "Movie",
      provider_ids: [{ external_id: "9308" }],
    }).includes("9308"),
  );
  assert.deepEqual(recordSearchValues(null), []);
});
test("production CSP disallows eval while permitting Next development debugging only in development", async () => {
  const original = process.env.NODE_ENV;
  try {
    process.env.NODE_ENV = "production";
    const productionHeaders = await nextConfig.headers();
    const productionCsp = productionHeaders[0].headers.find(
      (header) => header.key === "Content-Security-Policy",
    ).value;
    assert(!productionCsp.includes("unsafe-eval"));
    assert(productionCsp.includes("frame-ancestors 'none'"));
    assert(productionCsp.includes("object-src 'none'"));
    process.env.NODE_ENV = "development";
    const developmentHeaders = await nextConfig.headers();
    assert(
      developmentHeaders[0].headers
        .find((header) => header.key === "Content-Security-Policy")
        .value.includes("unsafe-eval"),
    );
  } finally {
    if (original === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = original;
  }
});
