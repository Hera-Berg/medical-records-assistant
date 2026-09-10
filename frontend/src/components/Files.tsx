/**
 * The folder, from inside the app.
 *
 * The record is a folder of plain files and that is a promise, not an
 * implementation detail: invariant 6 says to assume the app is dead in five
 * years and someone opens the folder in Finder. A screen that shows the folder
 * is how that promise stops being a claim in a document and becomes something
 * the owner can check — these are the real names, at the real paths, with the
 * real sizes.
 *
 * It is also the only screen in this app that **deletes**, and the tiers come
 * from the server, never from here. What this screen owes the person using it
 * is the sentence before the tap: an original that eleven claims were read off
 * says so before it goes, and a page that a rebuild will simply write again
 * says that too, because those are different decisions and only one of them is
 * reversible.
 *
 * The event log has no delete button at any tier of confirmation. Everything
 * else in the record is derived from it, so a button that can unlink a shard is
 * a button that can destroy the record — and the file manager, which is one
 * keystroke away, is the right place for a decision that big.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { PageHeader } from "../App";
import { Link } from "../router";
import type { FileContent, FileEntry, FileKind, FileListing } from "../types";
import { Empty, longStamp } from "./marks";

/**
 * What each kind of file is, as a chip.
 *
 * Same rule as every other mark in this app: the word carries the meaning and
 * the tint only makes it findable a second time. "Your record" and "Original"
 * are the two that matter — one of them cannot be deleted and the other cannot
 * be got back.
 */
const KINDS: Record<FileKind, { label: string; colour: string; ground: string }> = {
  log: { label: "Your record", colour: "var(--color-tier-rx)", ground: "#e8f0ea" },
  raw: { label: "Original", colour: "var(--color-tier-pt)", ground: "#f6eee7" },
  sidecar: { label: "About a file", colour: "var(--color-tier-file)", ground: "#eff0f1" },
  derived: { label: "Worked out", colour: "var(--color-tier-lab)", ground: "#e9edf7" },
  yours: { label: "Yours", colour: "var(--color-tier-dev)", ground: "#eeeaf5" },
  config: { label: "Setup", colour: "var(--color-tier-file)", ground: "#eff0f1" },
};

export function Files({
  path,
  navigate,
  version,
  onChanged,
  setHeader,
}: {
  path: string;
  navigate: (to: string) => void;
  version: number;
  onChanged: () => void;
  setHeader: (header: PageHeader) => void;
}) {
  const [data, setData] = useState<FileListing | null>(null);
  const [content, setContent] = useState<FileContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [local, setLocal] = useState(0);

  const reload = useCallback(() => setLocal((n) => n + 1), []);

  useEffect(() => {
    let live = true;
    setError(null);
    setContent(null);
    setConfirming(null);
    api
      .files(path)
      .then((result) => {
        if (!live) return;
        setData(result);
        if (result.entry?.text) {
          api
            .fileContent(result.entry.path)
            .then((text) => live && setContent(text))
            .catch((exc: Error) => live && setError(exc.message));
        }
      })
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [path, version, local]);

  /*
    Cleared when the screen changes folder, and *not* when it reloads. The
    reload is caused by the delete itself, so clearing there wiped the message
    in the same tick it was set — found by clicking through the real bundle.
  */
  useEffect(() => {
    setRefusal(null);
    setDone(null);
  }, [path]);

  useEffect(() => {
    setHeader({
      title: data?.entry ? data.entry.name : "Your folder",
      subtitle: data?.entry
        ? "One file, exactly as it is on your disk."
        : "Every file the record is made of. This is what stays behind if the app ever goes away.",
    });
  }, [data, setHeader]);

  const remove = (entry: FileEntry) => {
    api
      .deleteFile(entry.path)
      .then((result) => {
        setConfirming(null);
        // Said afterwards as well as before. A row that simply stops being
        // there is indistinguishable from a listing that refreshed oddly, and
        // whether it comes back is the thing worth knowing.
        setDone(
          result.rebuildable
            ? `${entry.name} is gone. Rebuild pages writes it again from your record.`
            : `${entry.name} is gone.`,
        );
        // A file view whose file has just gone has nothing left to show, so it
        // steps back to the folder that held it.
        if (data?.entry) navigate(fileHref(parentOf(entry.path)));
        else reload();
        onChanged();
      })
      .catch((exc: Error) => {
        setConfirming(null);
        setRefusal(exc.message);
      });
  };

  if (error) return <Empty>{error}</Empty>;
  if (!data) return <Empty>Reading your folder…</Empty>;

  return (
    <section>
      <nav className="flex flex-wrap items-baseline gap-1 border-b border-[color:var(--color-rule)] pb-2">
        {data.crumbs.map((crumb, index) => (
          <span key={crumb.path} className="flex items-baseline gap-1">
            {index > 0 ? (
              <span aria-hidden="true" className="text-[color:var(--color-muted)]">
                /
              </span>
            ) : null}
            {index === data.crumbs.length - 1 ? (
              <span className="font-semibold">{crumb.label}</span>
            ) : (
              <Link to={fileHref(crumb.path)} navigate={navigate}>
                {crumb.label}
              </Link>
            )}
          </span>
        ))}
      </nav>

      {data.folder ? (
        <p className="mt-2 text-[color:var(--color-muted)]">{data.folder}</p>
      ) : null}

      {done ? (
        <p
          className="mt-3 rounded-lg border border-l-4 px-4 py-2 no-print"
          style={{
            borderColor: "var(--color-rule)",
            borderLeftColor: "var(--color-accent)",
            background: "var(--color-accent-soft)",
          }}
        >
          {done}
        </p>
      ) : null}

      {refusal ? (
        <p
          className="mt-3 rounded-lg border border-l-4 px-4 py-2"
          style={{
            borderColor: "var(--color-rule)",
            borderLeftColor: "var(--color-alarm)",
            background: "var(--color-alarm-soft)",
          }}
        >
          <span className="font-semibold text-[color:var(--color-alarm)]">
            That was not deleted.
          </span>{" "}
          {refusal}
        </p>
      ) : null}

      {data.entry ? (
        <FileView
          entry={data.entry}
          content={content}
          navigate={navigate}
          confirming={confirming === data.entry.path}
          onAsk={() => setConfirming(data.entry!.path)}
          onCancel={() => setConfirming(null)}
          onDelete={() => remove(data.entry!)}
        />
      ) : (
        <Listing
          entries={data.entries}
          navigate={navigate}
          confirming={confirming}
          onAsk={setConfirming}
          onCancel={() => setConfirming(null)}
          onDelete={remove}
        />
      )}
    </section>
  );
}

function Listing({
  entries,
  navigate,
  confirming,
  onAsk,
  onCancel,
  onDelete,
}: {
  entries: FileEntry[];
  navigate: (to: string) => void;
  confirming: string | null;
  onAsk: (path: string) => void;
  onCancel: () => void;
  onDelete: (entry: FileEntry) => void;
}) {
  if (entries.length === 0) {
    return <Empty>This folder is empty.</Empty>;
  }
  return (
    <div className="table-wrap mt-3">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th className="w-32">What it is</th>
            <th className="w-24">Size</th>
            <th className="w-52">Changed</th>
            <th className="w-24 no-print"> </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <Row
              key={entry.path}
              entry={entry}
              navigate={navigate}
              confirming={confirming === entry.path}
              onAsk={() => onAsk(entry.path)}
              onCancel={onCancel}
              onDelete={() => onDelete(entry)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({
  entry,
  navigate,
  confirming,
  onAsk,
  onCancel,
  onDelete,
}: {
  entry: FileEntry;
  navigate: (to: string) => void;
  confirming: boolean;
  onAsk: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const kind = KINDS[entry.kind] ?? KINDS.yours;
  return (
    <>
      <tr>
        <td>
          <Link to={fileHref(entry.path)} navigate={navigate} className="font-semibold">
            {entry.is_dir ? `${entry.name}/` : entry.name}
          </Link>
          {entry.is_dir && entry.children != null ? (
            <span className="text-[color:var(--color-muted)]">
              {" "}
              — {entry.children} {entry.children === 1 ? "item" : "items"}
            </span>
          ) : null}
          {entry.artifact ? (
            <span className="text-[color:var(--color-muted)]">
              {" — "}
              <Link to={`/artifact/${entry.artifact}`} navigate={navigate}>
                open it as a document
              </Link>
            </span>
          ) : null}
        </td>
        <td>
          <span
            className="chip"
            title={entry.what}
            style={{ color: kind.colour, borderColor: kind.colour, background: kind.ground }}
          >
            {kind.label}
          </span>
        </td>
        <td className="whitespace-nowrap text-[color:var(--color-muted)]">
          {entry.is_dir ? "—" : size(entry.bytes)}
        </td>
        <td className="whitespace-nowrap text-[color:var(--color-muted)]">
          {longStamp(entry.modified) ?? "—"}
        </td>
        <td className="no-print">
          {entry.deletable ? (
            <button type="button" className="btn" onClick={onAsk}>
              Delete
            </button>
          ) : (
            <span className="text-[color:var(--color-muted)]" title={entry.refusal ?? undefined}>
              kept
            </span>
          )}
        </td>
      </tr>
      {confirming ? (
        <tr>
          <td colSpan={5} className="no-print">
            <Confirm entry={entry} onCancel={onCancel} onDelete={onDelete} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

/**
 * The sentence before the tap.
 *
 * Different for each kind, because the decisions are different: a derived page
 * comes back on the next rebuild, an original does not come back at all, and an
 * original that claims were read off takes their evidence with it. Saying
 * "are you sure?" to all three would be saying nothing to any of them.
 */
function Confirm({
  entry,
  onCancel,
  onDelete,
}: {
  entry: FileEntry;
  onCancel: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className="rounded-lg border border-l-4 px-4 py-2"
      style={{
        borderColor: "var(--color-rule)",
        borderLeftColor: "var(--color-warn)",
        background: "var(--color-warn-soft)",
      }}
    >
      <p>
        <span className="font-semibold">Delete {entry.name}?</span> {consequence(entry)}
      </p>
      <p className="mt-2 flex flex-wrap gap-3">
        <button type="button" className="btn btn-primary" onClick={onDelete}>
          Yes, delete it
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Keep it
        </button>
      </p>
    </div>
  );
}

function consequence(entry: FileEntry): string {
  if (entry.is_dir) return "The folder is empty, so nothing in your record goes with it.";
  if (entry.kind === "derived") {
    return "It is worked out from your record, so nothing is lost — Rebuild pages writes it again.";
  }
  if (entry.kind === "raw") {
    const read =
      entry.claims && entry.claims > 0
        ? `${entry.claims} ${entry.claims === 1 ? "entry" : "entries"} in your record ${
            entry.claims === 1 ? "was" : "were"
          } read from this document, and ${
            entry.claims === 1 ? "it stays" : "they stay"
          } exactly as ${entry.claims === 1 ? "it is" : "they are"}. What goes is the proof: `
        : "Nothing has been read from it yet. What goes is ";
    return `${read}the original cannot be got back, and every link to it stops opening anything.`;
  }
  if (entry.kind === "sidecar") {
    return "It is what the folder uses to explain the document beside it to someone with no app installed.";
  }
  return "It cannot be got back from here.";
}

function FileView({
  entry,
  content,
  navigate,
  confirming,
  onAsk,
  onCancel,
  onDelete,
}: {
  entry: FileEntry;
  content: FileContent | null;
  navigate: (to: string) => void;
  confirming: boolean;
  onAsk: () => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const kind = KINDS[entry.kind] ?? KINDS.yours;
  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className="chip"
          style={{ color: kind.colour, borderColor: kind.colour, background: kind.ground }}
        >
          {kind.label}
        </span>
        <span className="text-[color:var(--color-muted)]">
          {size(entry.bytes)} · changed {longStamp(entry.modified) ?? "at an unknown time"}
        </span>
        {entry.artifact ? (
          <Link to={`/artifact/${entry.artifact}`} navigate={navigate}>
            open it as a document
          </Link>
        ) : null}
        <span className="ml-auto no-print">
          {entry.deletable ? (
            <button type="button" className="btn" onClick={onAsk}>
              Delete
            </button>
          ) : null}
        </span>
      </div>

      <p className="mt-2 text-[color:var(--color-muted)]">{entry.what}</p>
      {entry.refusal ? (
        <p className="text-[color:var(--color-muted)]">{entry.refusal}</p>
      ) : null}

      {confirming ? (
        <div className="mt-3">
          <Confirm entry={entry} onCancel={onCancel} onDelete={onDelete} />
        </div>
      ) : null}

      {entry.text ? (
        content ? (
          <>
            {content.truncated ? (
              <p className="mt-3 text-[color:var(--color-muted)]">
                Showing the first {size(content.shown)} of {size(content.bytes)}. The rest
                is in the file.
              </p>
            ) : null}
            {/*
              Rendered as text, never as markup. These bytes arrived through
              somebody's sync client, and the one place in this app that hands
              vault bytes to a browser as a document does it behind a media-type
              allowlist and a policy that executes nothing.
            */}
            <pre className="mt-3 overflow-x-auto rounded-lg border border-[color:var(--color-rule)] bg-[color:var(--color-shade)] p-3 font-mono whitespace-pre-wrap">
              {content.text}
            </pre>
          </>
        ) : (
          <p className="mt-3 text-[color:var(--color-muted)]">Reading…</p>
        )
      ) : (
        <p className="mt-3 text-[color:var(--color-muted)]">
          This is not a text file, so it is not shown here.
          {entry.artifact ? " Open it as a document to see it." : ""}
        </p>
      )}
    </div>
  );
}

/** `/files` plus the path, with each segment encoded but the slashes kept. */
export function fileHref(path: string): string {
  if (!path) return "/files";
  return `/files/${path.split("/").map(encodeURIComponent).join("/")}`;
}

function parentOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut === -1 ? "" : path.slice(0, cut);
}

/**
 * A size in the units a person reads.
 *
 * Powers of two with the units people actually say. Nothing here is doing
 * arithmetic that reaches the record — this is a file listing, not a claim.
 */
function size(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
