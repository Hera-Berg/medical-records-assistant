/**
 * Render the interface headlessly and print it as text.
 *
 * There is no browser in this environment, and "read the rendered output" is
 * not satisfied by reading the components. This mounts the real bundle into a
 * DOM against a real running server, waits for it to settle, and prints what a
 * person would actually see — including the shapes that are awkward rather than
 * only the happy path.
 *
 *   node tools/render.mjs <path> [more paths...]
 *
 * Needs `health-agent serve` running on HEALTH_ORIGIN (default 127.0.0.1:7777).
 */

import { JSDOM } from "jsdom";
import { readFileSync, readdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const BUNDLE = resolve(here, "../../agent/server/static_files");
const ORIGIN = process.env.HEALTH_ORIGIN ?? "http://127.0.0.1:7777";

const scriptName = readdirSync(resolve(BUNDLE, "assets")).find((n) => n.endsWith(".js"));
const code = readFileSync(resolve(BUNDLE, "assets", scriptName), "utf8");

const BLOCK = new Set([
  "H1",
  "H2",
  "SECTION",
  "TABLE",
  "THEAD",
  "TBODY",
  "TR",
  "DETAILS",
  "P",
  "LI",
  "DT",
  "PRE",
  "NAV",
  "HEADER",
  "FOOTER",
  "DIV",
]);

/**
 * Turn a rendered DOM into something readable in a terminal.
 *
 * Text nodes are concatenated with their **original** spacing and collapsed
 * only at the end. Joining trimmed nodes with a space instead invents one
 * wherever an inline element sits before punctuation, so "(±10 days), from"
 * reads back as "(±10 days) , from" — which looks exactly like a real spacing
 * bug and cost a round of chasing one that was not there.
 */
function asText(root) {
  const chunks = [];
  let current = [];

  const flush = () => {
    const line = current.join("").replace(/\s+/g, " ").trim();
    if (line) chunks.push(line);
    current = [];
  };

  const walk = (el) => {
    for (const child of el.childNodes) {
      if (child.nodeType === 3) {
        current.push(child.textContent);
        continue;
      }
      if (child.nodeType !== 1) continue;
      const tag = child.tagName;
      if (tag === "SCRIPT" || tag === "STYLE") continue;
      if (BLOCK.has(tag)) flush();
      if (tag === "TD" || tag === "TH") current.push(" | ");
      walk(child);
      if (BLOCK.has(tag)) flush();
    }
  };

  walk(root);
  flush();
  return chunks.join("\n");
}

async function render(path) {
  const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div></body></html>`, {
    url: `${ORIGIN}${path}`,
    pretendToBeVisual: true,
    runScripts: "outside-only",
  });
  const { window } = dom;

  window.fetch = (input, init) =>
    fetch(typeof input === "string" ? `${ORIGIN}${input}` : input, init);
  window.matchMedia ??= () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  });
  globalThis.window = window;
  globalThis.document = window.document;
  // `navigator` is a getter-only global on modern node, so it is defined
  // rather than assigned.
  Object.defineProperty(globalThis, "navigator", {
    value: window.navigator,
    configurable: true,
  });

  window.eval(code);

  // React renders in a microtask; the screens then fetch. This is a diagnostic,
  // so it simply waits long enough rather than instrumenting the app.
  await new Promise((r) => setTimeout(r, 900));

  return asText(window.document.getElementById("root"));
}

for (const path of process.argv.slice(2)) {
  console.log("\n" + "=".repeat(78));
  console.log(path);
  console.log("=".repeat(78));
  try {
    console.log(await render(path));
  } catch (exc) {
    console.log("RENDER FAILED: " + exc.stack);
  }
}
process.exit(0);
