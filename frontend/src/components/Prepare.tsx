/**
 * Get ready for a visit: the person's own question, and a one-page sheet.
 *
 * The sheet is the existing consultation summary — built by code, every line
 * cited, no model involved. This screen only makes it two taps: type a question
 * (optional), then print. "See it first" shows the same preview the summary
 * screen shows, which writes nothing. Printing prepares the sheet for real —
 * one event and two files in `exports/` — and opens its print page.
 */

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { Link } from "../router";
import type { CalendarEvent, Summary } from "../types";
import { appointmentWhen } from "./appointments";
import { CheckIcon, ChevronLeft } from "./icons";
import { SheetView } from "./Summary";

const PREVIEW_DEBOUNCE_MS = 400;

export function Prepare({
  navigate,
  version,
  onChanged,
}: {
  navigate: (to: string) => void;
  version: number;
  onChanged: () => void;
}) {
  const wanted = new URLSearchParams(window.location.search).get("appointment");
  const [appointment, setAppointment] = useState<CalendarEvent | null>(null);
  const [today, setToday] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [preview, setPreview] = useState<Summary | null>(null);
  const [showing, setShowing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!wanted) return;
    let live = true;
    api
      .calendar()
      .then((result) => {
        if (!live) return;
        setToday(result.today);
        setAppointment(result.events.find((event) => event.id === wanted) ?? null);
      })
      .catch(() => live && setAppointment(null));
    return () => {
      live = false;
    };
  }, [wanted]);

  const label = appointment ? [appointment.title, appointment.professional].filter(Boolean).join(" with ") : "";

  // Debounced: each keystroke would otherwise re-project the whole record.
  useEffect(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api
        .previewSummary({ question, label })
        .then((result) => {
          setPreview(result);
          setError(null);
        })
        .catch((exc: Error) => setError(exc.message));
    }, PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(timer.current);
  }, [question, label, version]);

  const print = () => {
    setBusy(true);
    setError(null);
    api
      .prepareSummary({ question, label })
      .then((result) => {
        onChanged();
        // The print page is plain HTML served by the record itself, with no app
        // around it — what a browser prints well.
        window.location.assign(`/print/${encodeURIComponent(result.id)}`);
      })
      .catch((exc: ApiError) => setError(exc.message))
      .finally(() => setBusy(false));
  };

  const sections = preview?.sections ?? [];

  return (
    <div className="pt-page">
      <Link to="/" navigate={navigate} className="pt-back no-print">
        <ChevronLeft />
        Home
      </Link>
      <h1 className="pt-h1">{appointment ? appointment.title : "Get ready for a visit"}</h1>
      {appointment && today ? <p className="pt-headline">{appointmentWhen(appointment, today)}</p> : null}

      <div className="pt-field">
        <label htmlFor="visit-question" className="pt-label-strong">
          Your question
        </label>
        <p id="visit-question-hint" className="pt-muted">
          What you most want to ask. It goes on the sheet in your words. Optional.
        </p>
        <textarea
          id="visit-question"
          aria-describedby="visit-question-hint"
          className="pt-input"
          rows={3}
          value={question}
          maxLength={500}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="For example: Which heart tablet changed in August?"
        />
      </div>

      <section aria-labelledby="sheet-heading" className="pt-section">
        <h2 id="sheet-heading" className="pt-label-strong">On the sheet</h2>
        {preview ? (
          <ul className="pt-checklist">
            {sections.map((section) => (
              <li key={section.key}>
                <CheckIcon />
                <span>
                  {section.heading}
                  <span className="pt-muted">
                    {section.lines.length > 0
                      ? ` — ${section.lines.length} ${section.lines.length === 1 ? "line" : "lines"}`
                      : section.key === "question" && !question.trim()
                        ? " — add one above"
                        : " — nothing to list"}
                  </span>
                </span>
              </li>
            ))}
            {sections.some((section) => /question/i.test(section.key + section.heading)) ? null : (
              <li className="pt-unchecked">
                <span aria-hidden="true" className="pt-check-gap" />
                <span>
                  Your question<span className="pt-muted"> — not yet; type one above if you have one</span>
                </span>
              </li>
            )}
          </ul>
        ) : error ? null : (
          <p className="pt-muted" role="status">Reading your record…</p>
        )}
        <p className="pt-muted">One printed page. Every line says which of your documents it came from.</p>
      </section>

      {error ? (
        <p role="alert" className="pt-alert">
          {error}
        </p>
      ) : null}

      <div className="pt-actions">
        <button type="button" className="pt-btn pt-wide" onClick={print} disabled={busy}>
          {busy ? "Preparing the sheet…" : "Print the sheet"}
        </button>
        <button
          type="button"
          className="pt-btn-quiet pt-wide"
          aria-expanded={showing}
          aria-controls="sheet-preview"
          onClick={() => setShowing((open) => !open)}
          disabled={!preview}
        >
          {showing ? "Hide the preview" : "See it first"}
        </button>
      </div>

      {showing && preview ? (
        <section id="sheet-preview" className="pt-preview" aria-label="Preview of the sheet">
          <SheetView sheet={preview} />
        </section>
      ) : null}

      <p className="pt-muted pt-foot">
        Earlier sheets are kept in <Link to="/summary" navigate={navigate}>Appointment sheets</Link>.
      </p>
    </div>
  );
}
