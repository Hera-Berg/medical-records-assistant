/**
 * The outbox: what has been captured but not yet handed to the server.
 *
 * An in-memory retry queue was enough while everything came from a file the
 * user still had. A recording is different in the one way that matters: **it
 * does not exist anywhere else.** Close the tab, reload the page, drop off the
 * network mid-upload, and a recording held only in a React state variable is
 * gone — a minute of someone describing their symptoms in a waiting room, with
 * nothing to re-drop and no way to know it happened.
 *
 * So a recording is written to IndexedDB *before* the upload is attempted, and
 * removed only once the server has answered. Everything else — a dropped file,
 * a pasted screenshot — goes through the same queue, because a file the user
 * still has is no reason to be careless with it and one path is easier to
 * reason about than two.
 *
 * Draining happens on load, on `online`, and after each success. Order is
 * oldest first: a queue that uploads the newest recording first is a queue that
 * puts the oldest one last for ever if it is the one that keeps failing.
 *
 * IndexedDB can be unavailable — a private window, a browser configured to
 * refuse storage. That is a **degraded state, not a failure**: the queue falls
 * back to memory and says so, because refusing to record until storage works
 * would be worse than recording without a safety net.
 */

const DATABASE = "health-agent-outbox";
const STORE = "pending";
const VERSION = 1;

export interface Outgoing {
  id: number;
  blob: Blob;
  filename: string;
  source: "upload" | "paste" | "drop" | "recorder";
  /** Only ever set by the recorder, which genuinely knows it. */
  capturedTs: string | null;
  queuedAt: number;
  attempts: number;
  error: string | null;
}

let database: IDBDatabase | null = null;
let unavailable: string | null = null;

/** Rising ids for the memory fallback, which has no autoincrement. */
let memoryId = -1;
const memory: Outgoing[] = [];

function open(): Promise<IDBDatabase | null> {
  if (database) return Promise.resolve(database);
  if (unavailable) return Promise.resolve(null);
  return new Promise((resolve) => {
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(DATABASE, VERSION);
    } catch (exc) {
      unavailable = (exc as Error).message;
      resolve(null);
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
      }
    };
    request.onsuccess = () => {
      database = request.result;
      resolve(database);
    };
    request.onerror = () => {
      unavailable = request.error?.message ?? "this browser is not storing anything";
      resolve(null);
    };
    // A blocked upgrade never fires either handler. Nothing may wait for ever
    // on the path between a microphone and a stored recording.
    request.onblocked = () => {
      unavailable = "another tab is holding the queue open";
      resolve(null);
    };
  });
}

/** Whether the queue survives a reload. Shown in the interface when it does not. */
export function isDurable(): boolean {
  return unavailable === null;
}

export function whyNotDurable(): string | null {
  return unavailable;
}

function transact<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T | null> {
  return open().then(
    (db) =>
      new Promise<T | null>((resolve) => {
        if (!db) {
          resolve(null);
          return;
        }
        try {
          const request = work(db.transaction(STORE, mode).objectStore(STORE));
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => resolve(null);
        } catch {
          resolve(null);
        }
      }),
  );
}

export async function add(
  blob: Blob,
  filename: string,
  source: Outgoing["source"],
  capturedTs: string | null = null,
): Promise<Outgoing> {
  const item: Omit<Outgoing, "id"> = {
    blob,
    filename,
    source,
    capturedTs,
    queuedAt: Date.now(),
    attempts: 0,
    error: null,
  };
  const key = await transact<IDBValidKey>("readwrite", (store) => store.add(item));
  if (key === null) {
    const fallback = { ...item, id: memoryId-- } as Outgoing;
    memory.push(fallback);
    return fallback;
  }
  return { ...item, id: Number(key) } as Outgoing;
}

export async function all(): Promise<Outgoing[]> {
  const stored = (await transact<Outgoing[]>("readonly", (store) => store.getAll())) ?? [];
  // Oldest first. The failing item stays at the front rather than being
  // overtaken for ever by whatever was recorded most recently.
  return [...stored, ...memory].sort((a, b) => a.queuedAt - b.queuedAt);
}

export async function remove(id: number): Promise<void> {
  const index = memory.findIndex((item) => item.id === id);
  if (index >= 0) {
    memory.splice(index, 1);
    return;
  }
  await transact("readwrite", (store) => store.delete(id));
}

export async function update(item: Outgoing): Promise<void> {
  const index = memory.findIndex((existing) => existing.id === item.id);
  if (index >= 0) {
    memory[index] = item;
    return;
  }
  await transact("readwrite", (store) => store.put(item));
}
