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
  EntityDetail,
  FileContent,
  FileListing,
  Health,
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
   * `captured_ts` is deliberately not sent and there is no field for it. A
   * browser knows `File.lastModified`, which is a filesystem mtime rewritten by
   * every download, copy and sync client — not when a photograph was taken.
   * The server writes an explicit null, and the live camera and microphone
   * paths that genuinely know the moment arrive in phase 6.
   */
  capture: (files: File[], source: "upload" | "paste" | "drop", note?: string) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    form.append("source", source);
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

  rebuild: () => request<Record<string, unknown>>("/api/rebuild", { method: "POST" }),
};

export const artifactUrl = (short: string) => `/api/artifact/${encodeURIComponent(short)}`;
