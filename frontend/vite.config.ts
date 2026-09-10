import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import { execSync } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

// The build lands inside the Python package and is committed with it. Someone
// self-hosting this has Python and nothing else; a Vite build standing between
// `pip install` and a working app would put a node toolchain in the install
// story of a personal health record.
const OUT_DIR = resolve(here, "../agent/server/static_files");

function describe(command: string): string | null {
  try {
    return execSync(command, { cwd: here, encoding: "utf8" }).trim();
  } catch {
    return null;
  }
}

/**
 * Stamp the bundle with the commit it came from.
 *
 * A committed build can drift from the source that produced it, and the only
 * thing worse than a stale interface is a stale interface nobody can identify.
 * `/api/build` serves this back, so the question is answerable from a running
 * server rather than by reading the UI and wondering.
 */
function buildInfo() {
  return {
    name: "health-agent-build-info",
    closeBundle() {
      const commit = describe("git rev-parse HEAD");
      const dirty = describe("git status --porcelain -- .") ? true : false;
      mkdirSync(OUT_DIR, { recursive: true });
      writeFileSync(
        resolve(OUT_DIR, "build-info.json"),
        JSON.stringify(
          {
            commit,
            // Recorded plainly: a build made from an uncommitted working tree
            // cannot be traced to anything, and saying so is cheaper than
            // discovering it later.
            dirty,
            built: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
            source: "frontend/",
          },
          null,
          2,
        ) + "\n",
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwind(), buildInfo()],
  build: {
    outDir: OUT_DIR,
    emptyOutDir: true,
    // Everything is vendored into the bundle. No CDN, no webfont, no
    // source-map host: invariant 3 says the record makes no network calls, and
    // a build that reached out for anything would break that in the browser.
    assetsInlineLimit: 4096,
    sourcemap: false,
    target: "es2022",
  },
  server: {
    // Development only. Production is one origin, served by FastAPI.
    proxy: { "/api": "http://127.0.0.1:7777" },
  },
});
