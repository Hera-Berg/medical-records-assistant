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
  AskResponse,
  CaptureResponse,
  ConnectResult,
  EntityDetail,
  FileContent,
  FileListing,
  Health,
  ReaderInfo,
  ReadsOn,
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
  readAgain: (short: string) =>
    request<{ queued: string }>(`/api/artifact/${encodeURIComponent(short)}/read-again`, {
      method: "POST",
    }),

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

  /**
   * Ask a question about the record.
   *
   * POST for a read, deliberately: the question is content — "what did the
   * clinic say about my results" — and a GET would put it in the URL, in
   * browser history and in any proxy's log.
   *
   * Sends the conversation so far as plain words. The server re-derives what
   * each earlier question was about; a client that could send that derivation
   * could send one no question ever produced.
   *
   * Nothing this route touches is written. Asking appends no event, changes no
   * claim, and leaves no record of having been asked.
   */
  ask: (question: string, history: { question: string; answer: string }[] = []) =>
    request<AskResponse>("/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question, history }),
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
   * Point the record at the computer that reads your documents.
   *
   * One call, because it is one intention. The server reaches the box, asks
   * what it runs, checks it end to end — including whether it can actually read
   * words out of a picture — and writes `config.toml` only if all of that
   * passed. There is no route that saves an endpoint known not to work.
   *
   * The address guard runs server-side, first, before any credential is read.
   * There is no check in front of it here: a guard the browser could be talked
   * out of is not a guard.
   */
  connectEndpoint: (body: {
    base_url: string;
    model?: string;
    header?: string;
    scheme?: string;
  }) =>
    request<ConnectResult>("/api/settings/endpoint/connect", {
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
};

export const reader = {
  /** What reading on this computer needs, and where it has got to. Starts nothing. */
  info: () => request<ReaderInfo>("/api/reader"),

  /**
   * Start or continue the one-time download.
   *
   * The only call in this app that fetches anything from outside the user's
   * own machines, and it is made only from a button on a screen that has
   * already said the size, the hosts and where the files go.
   */
  download: (confirmBytes: number) =>
    request<ReaderInfo>("/api/reader/download", {
      method: "POST",
      headers: { "content-type": "application/json" },
      /* The exact figure the person was shown. The server refuses a start
         without it, or with one that no longer matches what it would fetch. */
      body: JSON.stringify({ confirm_bytes: confirmBytes }),
    }),
  cancel: () => request<ReaderInfo>("/api/reader/cancel", { method: "POST" }),
  retry: () => request<ReaderInfo>("/api/reader/retry", { method: "POST" }),

  /** Which computer reads documents — for this computer only, never the synced settings file. */
  choose: (reads_on: ReadsOn, sleep_after_minutes?: number, model?: string) =>
    request<ReaderInfo>("/api/settings/reader", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reads_on, sleep_after_minutes, model }),
    }),
};

export const artifactUrl = (short: string) => `/api/artifact/${encodeURIComponent(short)}`;
