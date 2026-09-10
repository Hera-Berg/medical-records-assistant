/**
 * One action, classified later.
 *
 * Drop anywhere on the window, paste anywhere, or pick a file. There is no
 * question about what kind of document it is — that is the model's job, and
 * asking the user at capture time is asking them to do it in a corridor.
 *
 * The upload is visible and non-blocking. It never gates the interface, and it
 * never waits for the inference box: the bytes are safe as soon as the server
 * answers, whether or not anything is awake to read them.
 *
 * A failed upload is retried from an in-memory queue and stays on screen until
 * it succeeds or the user dismisses it. The IndexedDB queue that survives a
 * page reload belongs with the recorder in phase 6, where a lost recording
 * cannot be re-dropped from a folder.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { CaptureResult } from "../types";

interface Pending {
  id: number;
  files: File[];
  source: "upload" | "paste" | "drop";
  attempts: number;
  error: string | null;
}

let nextId = 1;

export function useCapture(onCaptured: () => void) {
  const [pending, setPending] = useState<Pending[]>([]);
  const [recent, setRecent] = useState<CaptureResult[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const inFlight = useRef(false);

  const enqueue = useCallback((files: File[], source: Pending["source"]) => {
    if (files.length === 0) return;
    setPending((queue) => [
      ...queue,
      { id: nextId++, files, source, attempts: 0, error: null },
    ]);
  }, []);

  useEffect(() => {
    if (inFlight.current) return;
    const next = pending.find((item) => item.error === null);
    if (!next) return;

    inFlight.current = true;
    let cancelled = false;

    api
      .capture(next.files, next.source)
      .then((response) => {
        if (cancelled) return;
        setPending((queue) => queue.filter((item) => item.id !== next.id));
        setRecent((seen) => [...response.results, ...seen].slice(0, 8));
        setNote(response.note);
        onCaptured();
      })
      .catch((exc: Error) => {
        if (cancelled) return;
        const terminal = exc instanceof ApiError && exc.status >= 400 && exc.status < 500;
        setPending((queue) =>
          queue.map((item) =>
            item.id === next.id
              ? {
                  ...item,
                  attempts: item.attempts + 1,
                  // A 4xx will not become a 2xx by being sent again. Anything
                  // else is the server being unavailable, which is temporary
                  // and worth retrying.
                  error: terminal || item.attempts >= 4 ? exc.message : null,
                }
              : item,
          ),
        );
      })
      .finally(() => {
        inFlight.current = false;
        if (!cancelled) setPending((queue) => [...queue]);
      });

    return () => {
      cancelled = true;
    };
  }, [pending, onCaptured]);

  const dismiss = useCallback(
    (id: number) => setPending((queue) => queue.filter((item) => item.id !== id)),
    [],
  );

  const retry = useCallback(
    (id: number) =>
      setPending((queue) =>
        queue.map((item) =>
          item.id === id ? { ...item, error: null, attempts: 0 } : item,
        ),
      ),
    [],
  );

  return {
    enqueue,
    pending,
    recent,
    note,
    dismiss,
    retry,
    uploading: pending.filter((item) => item.error === null).length,
  };
}

/**
 * Drag-and-drop and paste, attached to the whole window.
 *
 * Anywhere on the page, not a target the user has to aim at. The overlay only
 * appears while a file is actually over the window — it is a state change, not
 * an animation, and it disappears the instant the drag ends.
 */
export function useWindowCapture(enqueue: (files: File[], source: Pending["source"]) => void) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  useEffect(() => {
    const onDragEnter = (event: DragEvent) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      depth.current += 1;
      setDragging(true);
    };
    const onDragOver = (event: DragEvent) => {
      if (event.dataTransfer?.types.includes("Files")) event.preventDefault();
    };
    const onDragLeave = () => {
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    };
    const onDrop = (event: DragEvent) => {
      if (!event.dataTransfer?.files.length) return;
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      enqueue(Array.from(event.dataTransfer.files), "drop");
    };
    const onPaste = (event: ClipboardEvent) => {
      const target = event.target as HTMLElement | null;
      // A paste into a text field is a paste into a text field.
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length === 0) return;
      event.preventDefault();
      enqueue(files, "paste");
    };

    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    window.addEventListener("paste", onPaste);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("paste", onPaste);
    };
  }, [enqueue]);

  return dragging;
}

export function DropOverlay() {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center border-4 border-[color:var(--color-ink)] bg-[color:var(--color-paper)]/90">
      <p className="text-lg font-semibold">
        Drop it anywhere. It is stored first and read later.
      </p>
    </div>
  );
}

export function CapturePanel({
  capture,
  onCaptured,
}: {
  capture: ReturnType<typeof useCapture>;
  onCaptured: () => void;
}) {
  const [text, setText] = useState("");
  const [noteError, setNoteError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const submitNote = async () => {
    if (!text.trim()) return;
    try {
      await api.note(text);
      setText("");
      setNoteError(null);
      onCaptured();
    } catch (exc) {
      setNoteError((exc as Error).message);
    }
  };

  return (
    <section className="no-print">
      <h1 className="border-b border-[color:var(--color-rule-strong)] pb-0.5 text-lg font-semibold">
        Add to the record
      </h1>
      <p className="mt-1">
        Drop a file anywhere on this page, paste one, or pick one below. Nothing asks
        what kind of document it is — that is worked out afterwards.
      </p>

      <div className="mt-2 flex flex-wrap items-baseline gap-3">
        <input
          ref={fileInput}
          type="file"
          multiple
          accept="image/*,application/pdf,audio/*,video/*,text/plain"
          className="hidden"
          onChange={(event) => {
            capture.enqueue(Array.from(event.target.files ?? []), "upload");
            event.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          className="border border-[color:var(--color-ink)] px-3 py-1 font-semibold"
        >
          Choose files
        </button>
        <span className="text-[color:var(--color-muted)]">
          Photographs of scripts, pathology PDFs, specialist letters.
        </span>
      </div>

      <h2 className="mt-5 border-b border-[color:var(--color-rule-strong)] pb-0.5 text-lg font-semibold">
        Write a note
      </h2>
      <p className="mt-1 text-[color:var(--color-muted)]">
        Recorded as patient-reported, dated when you write it. Voice notes arrive in
        phase 6.
      </p>
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={3}
        placeholder="Headaches started around Easter."
        className="mt-1 w-full border border-[color:var(--color-rule-strong)] p-2"
      />
      <div className="flex items-baseline gap-3">
        <button
          type="button"
          onClick={submitNote}
          disabled={!text.trim()}
          className="border border-[color:var(--color-ink)] px-3 py-1 font-semibold disabled:border-[color:var(--color-rule)] disabled:text-[color:var(--color-muted)]"
        >
          Record note
        </button>
        {noteError ? (
          <span className="text-[color:var(--color-tier-inf)]">{noteError}</span>
        ) : null}
      </div>

      <CaptureStatus capture={capture} />
    </section>
  );
}

export function CaptureStatus({ capture }: { capture: ReturnType<typeof useCapture> }) {
  if (capture.pending.length === 0 && capture.recent.length === 0) return null;

  return (
    <div className="mt-5">
      <h2 className="border-b border-[color:var(--color-rule-strong)] pb-0.5 text-lg font-semibold">
        Just added
      </h2>
      {capture.note ? (
        <p className="mt-1 text-[color:var(--color-muted)]">{capture.note}</p>
      ) : null}

      {capture.pending.length > 0 ? (
        <table className="mt-1">
          <tbody>
            {capture.pending.map((item) => (
              <tr key={item.id}>
                <td>{item.files.map((file) => file.name).join(", ")}</td>
                <td>
                  {item.error ? (
                    <span className="text-[color:var(--color-tier-inf)]">
                      not sent — {item.error}
                    </span>
                  ) : (
                    <span className="text-[color:var(--color-muted)]">
                      sending{item.attempts > 0 ? ` (attempt ${item.attempts + 1})` : ""}…
                    </span>
                  )}
                </td>
                <td className="w-32">
                  {item.error ? (
                    <>
                      <button
                        type="button"
                        onClick={() => capture.retry(item.id)}
                        className="border border-[color:var(--color-rule-strong)] px-2"
                      >
                        Retry
                      </button>{" "}
                      <button
                        type="button"
                        onClick={() => capture.dismiss(item.id)}
                        className="border border-[color:var(--color-rule-strong)] px-2"
                      >
                        Dismiss
                      </button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {capture.recent.length > 0 ? (
        <table className="mt-1">
          <thead>
            <tr>
              <th>File</th>
              <th className="w-32">Result</th>
              <th className="w-24">Hash</th>
            </tr>
          </thead>
          <tbody>
            {capture.recent.map((result, index) => (
              <tr key={`${result.short ?? result.filename}-${index}`}>
                <td>{result.filename}</td>
                <td>
                  {result.status === "failed" ? (
                    <span className="text-[color:var(--color-tier-inf)]">
                      {result.error}
                    </span>
                  ) : result.status === "reseen" ? (
                    <span title="Identical content was already in the record. Not stored twice.">
                      already had it
                    </span>
                  ) : (
                    "stored"
                  )}
                </td>
                <td className="font-mono">{result.short ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
