/**
 * One artefact, full size, with what the record knows about it.
 *
 * This is the end of every citation in the app. A claim is only as good as the
 * page it was read off, and the whole architecture rests on being able to get
 * from a value on screen to the photograph it came from in one click.
 *
 * The four timestamps are listed separately and `captured_ts` says "not known"
 * rather than borrowing from a neighbour. That gap is real information: it is
 * the difference between a photo taken today and one imported from three years
 * of files.
 */

import { useEffect, useState } from "react";
import { api, artifactUrl } from "../api";
import type { ArtifactMeta } from "../types";
import { Empty, Heading } from "./marks";

export function Artifact({ short, version }: { short: string; version: number }) {
  const [meta, setMeta] = useState<ArtifactMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setMeta(null);
    setError(null);
    api
      .artifactMeta(short)
      .then((result) => live && setMeta(result))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [short, version]);

  if (error) return <Empty>{error}</Empty>;
  if (!meta) return <Empty>Reading…</Empty>;

  return (
    <article>
      <div className="flex flex-wrap items-baseline gap-3 border-b border-[color:var(--color-rule-strong)] pb-1">
        <h1 className="text-lg font-semibold">Artefact {meta.short}</h1>
        <span className="text-[color:var(--color-muted)]">{meta.mime}</span>
        {meta.reseen_count > 0 ? (
          <span className="text-[color:var(--color-muted)]">
            seen again {meta.reseen_count}{" "}
            {meta.reseen_count === 1 ? "time" : "times"} since
          </span>
        ) : null}
        <a href={artifactUrl(meta.short)} className="ml-auto no-print">
          open the original
        </a>
      </div>

      {!meta.present ? (
        <Empty>
          The record says this exists, but its bytes are not in the vault. Restore it
          from a backup or the sync client's trash, or re-ingest the same file — every
          citation pointing here resolves to nothing until you do.
        </Empty>
      ) : (
        <Preview meta={meta} />
      )}

      <Heading>What the record knows</Heading>
      <dl className="grid grid-cols-[14rem_1fr] gap-x-4">
        <Row label="Added to the record" value={meta.ingested_ts} />
        <Row
          label="Taken or recorded"
          value={meta.captured_ts}
          absent="not known — a file picked from disk does not say when it was made"
        />
        <Row
          label="Document dated"
          value={meta.artifact_ts}
          absent="not known — this needs the document to be read"
        />
        <Row label="Where it came from" value={meta.source} />
        <Row label="File" value={meta.path} mono />
        <Row label="Full hash" value={meta.digest} mono />
        <Row
          label="Size"
          value={meta.bytes != null ? `${meta.bytes.toLocaleString()} bytes` : null}
        />
      </dl>
      <p className="mt-2 text-[color:var(--color-muted)]">
        The original is never modified. Everything the model sees is a working copy that
        is not kept.
      </p>
    </article>
  );
}

function Preview({ meta }: { meta: ArtifactMeta }) {
  const url = artifactUrl(meta.short);
  const kind = meta.mime.split("/")[0];

  if (!meta.renders_inline) {
    return (
      <p className="mt-2">
        This file type is not displayed in the page — it is served as a download
        instead, because the vault syncs from storage this app does not control and a
        document that can carry script must not run here.{" "}
        <a href={url}>Save it</a> to open it yourself.
      </p>
    );
  }
  if (kind === "image") {
    return (
      <img
        src={url}
        alt={`Artefact ${meta.short}`}
        className="mt-2 max-w-full border border-[color:var(--color-rule)]"
      />
    );
  }
  if (meta.mime === "application/pdf") {
    return (
      <object
        data={url}
        type="application/pdf"
        className="mt-2 h-[36rem] w-full border border-[color:var(--color-rule)]"
      >
        <p>
          Your browser will not display this PDF in the page. <a href={url}>Open it</a>.
        </p>
      </object>
    );
  }
  if (kind === "audio") {
    return <audio controls src={url} className="mt-2 w-full" />;
  }
  if (kind === "video") {
    return (
      <video
        controls
        src={url}
        className="mt-2 max-h-[36rem] w-full border border-[color:var(--color-rule)]"
      />
    );
  }
  return (
    <p className="mt-2">
      <a href={url}>Open the original</a>.
    </p>
  );
}

function Row({
  label,
  value,
  absent,
  mono,
}: {
  label: string;
  value: string | null;
  absent?: string;
  mono?: boolean;
}) {
  return (
    <>
      <dt className="text-[color:var(--color-muted)]">{label}</dt>
      <dd className={mono ? "font-mono break-all" : ""}>
        {value ?? (
          <span className="text-[color:var(--color-muted)]">{absent ?? "not known"}</span>
        )}
      </dd>
    </>
  );
}
