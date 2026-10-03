// Tests for safeNext() in app/static/login.html: the login page may send the user back only
// to a same-origin relative path. The function is cut out of the page so the real code is tested.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "../../app/static/login.html"), "utf8");
const body = html.match(/\/\/ BEGIN safeNext([\s\S]*?)\/\/ END safeNext/)[1];
const safeNext = new Function(`${body}; return safeNext;`)();
const ORIGIN = "https://app.example.test";

test("a relative path with its query is kept whole", () => {
  assert.equal(safeNext("/v1/sprint?assessment=V1A000001", ORIGIN), "/v1/sprint?assessment=V1A000001");
  assert.equal(safeNext("/dashboard", ORIGIN), "/dashboard");
  assert.equal(safeNext("/players-dashboard?team=TEAM000001&x=1", ORIGIN), "/players-dashboard?team=TEAM000001&x=1");
});

test("anything that could leave this site is refused", () => {
  const bad = ["//evil.example", "///evil.example", "/\\evil.example", "\\evil.example", "\\\\evil.example",
    "https://evil.example", "http://evil.example/x", "javascript:alert(1)", "data:text/html,x", "evil.example",
    "/\t/evil.example", "/\n/evil.example", "/\u0000", "", " /x", null, undefined, 5, {},
    "/..//evil.example", "/./\\evil.example", "/a/..//evil.example", "/" + "a".repeat(600)];
  for (const value of bad) assert.equal(safeNext(value, ORIGIN), null, JSON.stringify(value));
});

test("the fragment is dropped and encoding is not undone", () => {
  assert.equal(safeNext("/v1/sprint?assessment=A#frag", ORIGIN), "/v1/sprint?assessment=A");
  assert.equal(safeNext("/a%2F%2Fb", ORIGIN), "/a%2F%2Fb");
  assert.equal(safeNext("/x%0d%0ay", ORIGIN), "/x%0d%0ay");   // stays encoded, stays on this site
});

test("login uses it and falls back to the landing page", () => {
  assert.match(html, /const next = safeNext\(requestedNext, location\.origin\);/);
  assert.match(html, /location\.assign\(next \|\| fallback\);/);
});
