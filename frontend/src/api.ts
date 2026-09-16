/**
 * Talking to the server.
 *
 * Same origin, always. There is no base URL to configure and no second host to
 * reach: the SPA is served by the same FastAPI process that answers these
 * calls, which is what makes the port the only boundary the record needs.
 *
 * Every failure carries the server's own sentence where there is one. The
 * refusals in this project are written to be read — they name the vault, the
 * artefact and what to do next — so replacing them with "request failed" throws
 * away the most useful thing in the response.
 */

import type {
  ArtifactMeta,
  CaptureResponse,
  EndpointCheck,
  EndpointModels,
  EntityDetail,
  FileContent,
  FileListing,
  Health,
  ReviewQueue,
  Settings,
  Summary,
  SummaryList,
  SummaryResponse,
  Timeline,
  WikiIndex,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { accept: "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    // The server is not answering at all. Distinct from a server that answered
    // with a problem, and the interface says so differently.
    throw new ApiError("the local server is not responding", 0);
  }
  if (!response.ok) {
    throw new ApiError(await detailOf(response), response.status);
  }
  return (await response.json()) as T;
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* not JSON; fall through to the status line */
  }
  return `${response.status} ${response.statusText}`.trim();
}

export const api = {
  health: () => request<Health>("/api/health"),
  wiki: () => request<WikiIndex>("/api/wiki"),
  entity: (id: string) => request<EntityDetail>(`/api/wiki/${encodeURIComponent(id)}`),
  artifactMeta: (short: string) =>
    request<ArtifactMeta>(`/api/artifact/${encodeURIComponent(short)}/meta`),

  timeline: (params: {
    from?: string;
    to?: string;
    subject?: string;
    tier?: string[];
    limit?: number;
    offset?: number;
  }) => {
    const query = new URLSearchParams();
    if (params.from) query.set("from", params.from);
    if (params.to) query.set("to", params.to);
    if (params.subject) query.set("subject", params.subject);
    for (const tier of params.tier ?? []) query.append("tier", tier);
    if (params.limit != null) query.set("limit", String(params.limit));
    if (params.offset != null) query.set("offset", String(params.offset));
    const suffix = query.toString();
    return request<Timeline>(`/api/timeline${suffix ? `?${suffix}` : ""}`);
  },

  /**
   * Send files.
   *
   * `capturedTs` is sent **only** by the recorder, and there is no path that
   * sends it for anything else. A browser knows `File.lastModified`, which is a
   * filesystem mtime rewritten by every download, copy and sync client — not
   * when a photograph was taken — so for a dropped or picked file the server
   * writes an explicit null. A recording made by this page is the one case
   * where the moment is genuinely known, because this page watched it happen;
   * the server still checks it rather than trusting it.
   */
  capture: (
    files: File[],
    source: "upload" | "paste" | "drop" | "recorder",
    capturedTs?: string | null,
    note?: string,
  ) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    form.append("source", source);
    if (capturedTs) form.append("captured_ts", capturedTs);
    if (note) form.append("note", note);
    return request<CaptureResponse>("/api/capture", { method: "POST", body: form });
  },

  note: (text: string) => {
    const form = new FormData();
    form.append("text", text);
    return request<{ event: string; ts: string; text: string }>("/api/capture/text", {
      method: "POST",
      body: form,
    });
  },

  files: (path: string) =>
    request<FileListing>(`/api/files?path=${encodeURIComponent(path)}`),

  fileContent: (path: string) =>
    request<FileContent>(`/api/files/content?path=${encodeURIComponent(path)}`),

  /**
   * Remove one file or one empty folder.
   *
   * The server decides what may go — the log never, an original deliberately,
   * anything derived freely. The screen asks first and shows the server's own
   * sentence when the answer is no.
   */
  deleteFile: (path: string) =>
    request<{ deleted: string; kind: string; was_dir: boolean; rebuildable: boolean }>(
      `/api/files?path=${encodeURIComponent(path)}`,
      { method: "DELETE" },
    ),

  review: () => request<ReviewQueue>("/api/review"),

  /**
   * Decide one review item.
   *
   * The server re-derives which claims this item folded and decides all of
   * them, so one tap on a fact two documents agree about emits one
   * confirmation per document and the queue does not come back to ask again.
   *
   * A `409` is not a failure to report as one: it means the item was decided
   * somewhere else — the other device, or another tab — and the body carries
   * both the explanation and the refreshed queue. The caller shows the sentence
   * and swaps the list, which is what the user needs either way.
   */
  decide: async (
    id: string,
    body: {
      action: string;
      value?: string;
      target?: string;
      occurred_at?: { value: string; precision: string; uncertainty_days: number };
    },
  ): Promise<ReviewQueue> => {
    const response = await fetch(`/api/review/${encodeURIComponent(id)}`, {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => {
      throw new ApiError("the local server is not responding", 0);
    });
    if (response.status === 409) return (await response.json()) as ReviewQueue;
    if (!response.ok) throw new ApiError(await detailOf(response), response.status);
    return (await response.json()) as ReviewQueue;
  },

  rebuild: () => request<Record<string, unknown>>("/api/rebuild", { method: "POST" }),

  summaries: () => request<SummaryList>("/api/summary"),

  summary: (id: string) =>
    request<SummaryResponse>(`/api/summary/${encodeURIComponent(id)}`),

  /**
   * The sheet that would be prepared, without preparing it.
   *
   * Writes nothing and records nothing, so someone can read exactly what they
   * are about to hand a clinician before an event and two files exist in their
   * folder.
   */
  previewSummary: (body: { question?: string; label?: string; since?: string | null }) =>
    request<Summary>("/api/summary/preview", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),

  /** Prepare it for real: one event, and two files in `exports/`. */
  prepareSummary: (body: { question?: string; label?: string; since?: string | null }) =>
    request<Summary>("/api/summary", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),

  settings: () => request<Settings>("/api/settings"),

  /**
   * Record where the folder already lives.
   *
   * Moves nothing and contacts no service. The server refuses with a sentence
   * worth reading when a sync client has forked `config.toml`, which is the one
   * moment writing to it could lose the whole file.
   */
  setSyncProfile: (profile: string) =>
    request<Settings>("/api/settings/sync-profile", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ profile }),
    }),

  /**
   * Write the inference endpoint into `config.toml`.
   *
   * The address guard runs on the server before anything is written, so a
   * public host comes back as a refusal with the reason in it. There is no
   * client-side check standing in front of that: a guard the browser could be
   * talked out of is not a guard.
   */
  setEndpoint: (body: {
    base_url: string;
    model: string;
    header: string;
    scheme: string;
  }) =>
    request<Settings>("/api/settings/endpoint", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),

  /**
   * Store the key in this computer's keychain.
   *
   * One way only. There is no `getEndpointKey`, no route that would answer one,
   * and nothing in the response that carries the key, a prefix of it or its
   * length — see MODELS.md, "The browser never sees the key".
   */
  setEndpointKey: (key: string) =>
    request<Settings>("/api/settings/endpoint/key", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ key }),
    }),

  /** What the box says it is running, verbatim. An id is not a name to retype. */
  endpointModels: (body: { base_url: string; header: string; scheme: string }) =>
    request<EndpointModels>("/api/settings/endpoint/models", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),

  /** Run the startup probe against what is in the form. Writes nothing. */
  testEndpoint: (body: {
    base_url: string;
    model: string;
    header: string;
    scheme: string;
  }) =>
    request<EndpointCheck>("/api/settings/endpoint/test", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
};

export const artifactUrl = (short: string) => `/api/artifact/${encodeURIComponent(short)}`;
