/**
 * Which record this tab shows: the person's own, or the demo record.
 *
 * Decided once, when the tab loads, and sent as `X-Health-Record` on every
 * request this tab makes to the server. The server routes on that header before
 * anything else (agent/server/sample_record.py), so a tab showing someone's own
 * record keeps reading their own record — Ask included — even after another tab
 * switches to the demo. Switching record reloads the page, which is the only way
 * a tab's record changes.
 */

export type RecordKind = "own" | "demo";

let current: RecordKind = "own";
let available = false;
const original = window.fetch.bind(window);

export function recordKind(): RecordKind {
  return current;
}

/** True when this tab shows the demo record. */
export function isDemoRecord(): boolean {
  return current === "demo";
}

/** Whether this copy of the app carries the demo record at all. */
export function demoAvailable(): boolean {
  return available;
}

function sameOrigin(url: string): boolean {
  try {
    return new URL(url, window.location.href).origin === window.location.origin;
  } catch {
    return false;
  }
}

/** Read this tab's record from the server, then tag every request with it. */
export async function pinRecord(): Promise<void> {
  try {
    const response = await original("/api/demo", { cache: "no-store" });
    if (response.ok) {
      const body = (await response.json()) as { record?: string; available?: boolean };
      current = body.record === "demo" ? "demo" : "own";
      available = body.available === true;
    }
  } catch {
    // The setup server and an unreachable server both mean: the person's own.
    current = "own";
  }
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!sameOrigin(url)) return original(input, init);
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set("X-Health-Record", current);
    return original(input, { ...init, headers });
  };
}

/**
 * Move this browser to another record and say where to go. The caller reloads
 * the page there, so the new record is pinned from the start.
 */
export async function switchRecord(to: RecordKind, workspace: "patient" | "clinician"): Promise<string> {
  const response = await original(to === "demo" ? "/api/demo/enter" : "/api/demo/leave", {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace }),
  });
  const body = (await response.json().catch(() => ({}))) as { path?: string; detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "The demo record could not be opened.");
  return to === "demo" ? body.path ?? "/" : workspace === "clinician" ? "/clinician" : "/overview";
}
