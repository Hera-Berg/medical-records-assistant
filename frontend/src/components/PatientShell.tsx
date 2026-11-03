/**
 * The patient and carer workspace's frame: a top bar, a tab bar on a phone,
 * and one "Add" button.
 *
 * Simpler than the clinician workspace on purpose. That one is a professional's
 * desk — dense, detailed, every view a click away. This is for someone at home
 * or in a waiting room: five places to go, one thing to do ("Add"), and every
 * screen a single readable column. Everything that used to be in the menu is
 * still reachable, from More.
 *
 * Accessibility is part of the layout, not an addition to it: a skip link, one
 * `<main>`, the current tab marked with `aria-current`, a native `<dialog>` for
 * the Add sheet (so focus is held inside it and Escape closes it), targets at
 * least 44 pixels tall, and every icon beside its own word.
 */

import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Link } from "../router";
import type { Health } from "../types";
import type { useCapture } from "./Capture";
import { Mark } from "./Sidebar";
import {
  AskIcon,
  CameraIcon,
  CloseIcon,
  FileIcon,
  HistoryIcon,
  HomeIcon,
  MicIcon,
  MoreIcon,
  PencilIcon,
  PillIcon,
  PlusIcon,
  CalendarIcon,
} from "./icons";

type Capture = ReturnType<typeof useCapture>;

interface Tab {
  to: string;
  label: string;
  icon: React.ReactNode;
  /** Other first path segments that belong to this tab. */
  also: string[];
  /** Shown in the phone's bar as well as the top bar. */
  phone: boolean;
}

const TABS: Tab[] = [
  { to: "/", label: "Home", icon: <HomeIcon />, also: ["overview"], phone: true },
  { to: "/medicines", label: "Medicines", icon: <PillIcon />, also: ["allergies", "conditions", "symptoms", "record"], phone: true },
  { to: "/timeline", label: "History", icon: <HistoryIcon />, also: ["artifact", "observation"], phone: true },
  { to: "/calendar", label: "Appointments", icon: <CalendarIcon />, also: ["prepare", "summary"], phone: false },
  { to: "/ask", label: "Ask", icon: <AskIcon />, also: [], phone: true },
  {
    to: "/more",
    label: "More",
    icon: <MoreIcon />,
    also: ["documents", "care-team", "notifications", "profile", "settings", "files", "review", "add", "welcome"],
    phone: true,
  },
];

function activeTab(first: string | undefined, wide: boolean): string {
  const segment = first ?? "";
  for (const tab of TABS) {
    const own = tab.to.slice(1);
    if (segment === own || tab.also.includes(segment)) {
      // Appointments has no phone tab; on a phone it lives under More.
      if (!wide && !tab.phone) return "/more";
      return tab.to;
    }
  }
  return segment === "" ? "/" : "";
}

export function PatientShell({
  first,
  health,
  capture,
  navigate,
  children,
  notice,
}: {
  first: string | undefined;
  health: Health | null;
  capture: Capture;
  navigate: (to: string) => void;
  children: React.ReactNode;
  /** Something the whole app needs to say, above the screen. */
  notice?: React.ReactNode;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState<string | null>(null);
  const wide = useWide();
  const current = activeTab(first, wide);

  useEffect(() => {
    let live = true;
    api
      .profile()
      .then((profile) => {
        if (!live) return;
        const values = (profile as unknown as { values?: Record<string, string> }).values ?? {};
        const shown = values.full_name || values.preferred_name || null;
        setName(shown && shown !== "Not provided" ? shown : null);
      })
      .catch(() => live && setName(null));
    return () => {
      live = false;
    };
  }, []);

  const waiting = health?.review.total ?? 0;

  return (
    <div className="pt-shell" data-workspace="patient-carer">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="pt-top no-print">
        <div className="pt-top-inner">
          <Link to="/" navigate={navigate} className="pt-brand">
            <span className="pt-brand-mark">
              <Mark />
            </span>
            <span className="pt-brand-name">CareMind</span>
            <span className="sr-only"> — home</span>
          </Link>
          {name ? (
            <span className="pt-who" title="Whose record this is">
              <span className="sr-only">Record of </span>
              {name}
            </span>
          ) : null}
          {health?.vault.demo ? (
            <span className="pt-demo" title="Everything in this record was invented so the screens have something to show.">
              Demo
            </span>
          ) : null}

          <nav className="pt-nav" aria-label="Main">
            <ul>
              {TABS.map((tab) => (
                <li key={tab.to}>
                  <Link
                    to={tab.to}
                    navigate={navigate}
                    ariaCurrent={current === tab.to ? "page" : undefined}
                    className="pt-nav-link"
                  >
                    {tab.label}
                    {tab.to === "/more" && waiting > 0 ? (
                      <span className="pt-count">
                        {waiting}
                        <span className="sr-only"> to check</span>
                      </span>
                    ) : null}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          <button type="button" className="pt-add" onClick={() => setAdding(true)} aria-haspopup="dialog">
            <PlusIcon size={18} />
            <span>Add</span>
          </button>
        </div>
      </header>

      <Uploading capture={capture} navigate={navigate} />

      <main id="main" tabIndex={-1} className="pt-main">
        {notice}
        {children}
      </main>

      <nav className="pt-tabs no-print" aria-label="Main">
        <ul>
          {TABS.filter((tab) => tab.phone).map((tab) => (
            <li key={tab.to}>
              <Link
                to={tab.to}
                navigate={navigate}
                ariaCurrent={current === tab.to ? "page" : undefined}
                className="pt-tab"
              >
                <span className="pt-tab-icon">
                  {tab.icon}
                  {tab.to === "/more" && waiting > 0 ? <span className="pt-dot" aria-hidden="true" /> : null}
                </span>
                <span>{tab.label}</span>
                {tab.to === "/more" && waiting > 0 ? <span className="sr-only">, {waiting} to check</span> : null}
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      <AddSheet open={adding} onClose={() => setAdding(false)} capture={capture} navigate={navigate} />
    </div>
  );
}

/** Whether the screen is wide enough for the top bar to carry every tab. */
function useWide() {
  const query = "(min-width: 768px)";
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = () => setWide(list.matches);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, []);
  return wide;
}

/**
 * Sending, said in one line under the top bar. Never blocks anything: the
 * bytes are in this browser's outbox until the server has them.
 */
function Uploading({ capture, navigate }: { capture: Capture; navigate: (to: string) => void }) {
  const failed = capture.pending.filter((item) => item.error !== null).length;
  const sending = capture.uploading;
  const [justAdded, setJustAdded] = useState<number>(0);
  const seen = useRef(capture.recent.length);

  useEffect(() => {
    const added = capture.recent.length - seen.current;
    seen.current = capture.recent.length;
    if (added <= 0) return;
    setJustAdded(added);
    const timer = window.setTimeout(() => setJustAdded(0), 8000);
    return () => window.clearTimeout(timer);
  }, [capture.recent.length]);

  let text: React.ReactNode = null;
  if (sending > 0) text = `Saving ${sending} ${sending === 1 ? "item" : "items"} to your record…`;
  else if (failed > 0)
    text = (
      <>
        {failed} {failed === 1 ? "item was" : "items were"} not saved.{" "}
        <Link to="/add" navigate={navigate}>
          See what happened
        </Link>
      </>
    );
  else if (justAdded > 0)
    text = (
      <>
        Saved to your record. It will be read soon, and anything that needs your yes or no will be on Home.{" "}
        <Link to="/timeline" navigate={navigate}>
          See it in History
        </Link>
      </>
    );

  return (
    <div className="pt-status no-print" role="status" aria-live="polite">
      {text ? <p>{text}</p> : null}
    </div>
  );
}

/**
 * Four ways in, no questions about what it is. A native modal dialog: the
 * browser holds focus inside it, Escape closes it, and the page behind is inert.
 */
function AddSheet({
  open,
  onClose,
  capture,
  navigate,
}: {
  open: boolean;
  onClose: () => void;
  capture: Capture;
  navigate: (to: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const photo = useRef<HTMLInputElement>(null);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  const take = (files: FileList | null) => {
    const list = Array.from(files ?? []);
    if (list.length > 0) capture.enqueue(list, "upload");
    onClose();
  };

  const go = (to: string) => {
    onClose();
    navigate(to);
  };

  return (
    <dialog
      ref={dialog}
      className="pt-sheet"
      aria-labelledby="add-title"
      onClose={onClose}
      onClick={(event) => {
        // A click on the backdrop lands on the dialog itself.
        if (event.target === dialog.current) onClose();
      }}
    >
      <div className="pt-sheet-body">
        <div className="pt-sheet-head">
          <h2 id="add-title">Add</h2>
          <button type="button" className="pt-icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </div>
        <ul className="pt-choices">
          <li>
            <button type="button" className="pt-choice is-primary" onClick={() => photo.current?.click()}>
              <CameraIcon />
              <span>Take a photo</span>
            </button>
          </li>
          <li>
            <button type="button" className="pt-choice" onClick={() => file.current?.click()}>
              <FileIcon />
              <span>Choose a file</span>
            </button>
          </li>
          <li>
            <button type="button" className="pt-choice" onClick={() => go("/add?voice")}>
              <MicIcon />
              <span>Say it out loud</span>
            </button>
          </li>
          <li>
            <button type="button" className="pt-choice" onClick={() => go("/add?note")}>
              <PencilIcon />
              <span>Write a note</span>
            </button>
          </li>
        </ul>
        <p className="pt-muted">You don't need to say what it is. A photo of a script, a letter or a results page is enough.</p>
        <input
          ref={photo}
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            take(event.target.files);
            event.target.value = "";
          }}
        />
        <input
          ref={file}
          type="file"
          multiple
          accept="image/*,application/pdf,audio/*,text/plain"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            take(event.target.files);
            event.target.value = "";
          }}
        />
      </div>
    </dialog>
  );
}
