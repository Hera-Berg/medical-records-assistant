/**
 * The shell: the menu, the page heading, what needs a person, and the screen.
 *
 * Shaped like the assistants people already know how to use: a button at the
 * top left opens a menu down the left, and the page is one readable column.
 * Overview is the home screen. The patient profile lives at `/profile`,
 * questions at `/ask`, and the timeline at `/timeline`.
 *
 * Timeline, record, one entity, one artefact, the folder and settings — plus
 * capture, which is not a screen so much as something the whole window does.
 * The recorder lives inside capture rather than beside it: speaking into the
 * record is the same action as dropping a file in, arriving through a different
 * input, and a tab of its own would imply a conversation.
 *
 * The review inbox is here, and so is the consultation summary. The inbox is
 * the only screen that puts anything into the record, which is why it is the
 * one place a proposed value is shown at all — everywhere else a gated value
 * would be read as current. The summary screen writes too, but only a document:
 * it records that a sheet was prepared and never changes a claim.
 *
 * Asking the record is here too, and has its own tab rather than sitting inside
 * capture: it is a different mode, not a way of putting something in. It is
 * called "Ask your record" and never "chat" — the word promises advice, and the
 * first thing anyone types under that promise is "should I be worried about
 * this", which this application refuses. The label is the cheapest place to
 * stop the question being asked.
 *
 * What made it safe to build is what took until phase 10 to exist: retrieval
 * that is deterministic code, a citation on every sentence or no sentence, and
 * a refusal classifier in front of the whole thing. A chat box wired to the
 * model without those is the one screen this project must not ship, because it
 * would answer health questions fluently and cite nothing.
 *
 * **The heading is owned by whichever screen knows the answer.** The shell sets
 * a default from the route; an entity or an artefact replaces it once loaded,
 * because "Perindopril" is a better page title than "Record entry" and the
 * shell cannot know it without fetching what the screen is already fetching.
 */

import { createElement, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Link, useRoute } from "./router";
import type { Health } from "./types";
import { rememberInstallation } from "./installation";
import { Artifact } from "./components/Artifact";
import { Ask } from "./components/Ask";
import { Attention } from "./components/Attention";
import { Boundary } from "./components/Boundary";
import { CapturePanel, DropOverlay, useCapture, useWindowCapture } from "./components/Capture";
import { Entity } from "./components/Entity";
import { Files } from "./components/Files";
import { ClinicalRecord } from "./components/ClinicalRecord";
import { Record } from "./components/Record";
import { Review } from "./components/Review";
import { Settings } from "./components/Settings";
import { MenuIcon, Sidebar } from "./components/Sidebar";
import { SummaryScreen } from "./components/Summary";
import { DoctorNotes } from "./components/DoctorNotes";
import { ClinicalTimeline } from "./components/ClinicalTimeline";
import { Welcome } from "./components/Welcome";
import { ClinicianComparisonProvider } from "./comparison";
import { ClinicianLanguage } from "./recordLanguage";
import { PatientIdentity } from "./components/PatientIdentity";
import { OriginalObservation } from "./components/OriginalObservation";
import { ClinicianLogin } from "./components/ClinicianLogin";
import { useClinicianSession, authRequest, notifyAccountChange, clinicianPath } from "./clinicianAuth";
import { CareTeam } from "./components/CareTeam";
import { MedicalDocuments } from "./components/MedicalDocuments";
import { SubmittedUpdates } from "./components/SubmittedUpdates";
import { SharedCare, Notifications } from "./components/SharedCare";
import { ClinicianHome } from "./components/ClinicianHome";
import { useWorkspace } from "./workspace";

/** How often the sidebar and banners refresh. The route is cheap and opens no socket. */
const HEALTH_INTERVAL_MS = 5000;

/** Wide enough for the menu to sit beside the page rather than over it. */
const WIDE = "(min-width: 1024px)";
export interface PageHeader {
  title: string;
  subtitle: string;
  /** Shown beside the title. The entity's status lives here rather than being
   *  printed a second time inside the panel below it. */
  badge?: React.ReactNode;
}

export function App() {
  const [route, navigateBase] = useRoute();
  const workspace = useWorkspace(route.path);
  const {session, checked, setSession} = useClinicianSession(route.path);
  const navigate = useCallback((to: string) => navigateBase(workspace === "clinician" && session ? clinicianPath(to) : to), [navigateBase, workspace, session]);
  const [signOutError, setSignOutError] = useState<string|null>(null);
  const signingIn = route.path.replace(/\/$/, "") === "/clinician/sign-in";
  const needsClinician = workspace === "clinician" || ["doctor-notes", "progress-notes"].includes(route.segments[0] ?? "");
  useEffect(() => {
    if (checked && needsClinician && !session && !signingIn) navigateBase("/clinician/sign-in");
    if (checked && session && signingIn) navigateBase("/clinician");
    if (checked && session && workspace === "clinician" && !signingIn) {
      const canonical = clinicianPath(route.path);
      if (canonical !== route.path) navigateBase(canonical);
    }
  }, [checked, needsClinician, session, signingIn, navigateBase, workspace, route.path]);
  const signOut = async () => {
    try {
      await authRequest("logout", {}); setSession(null); notifyAccountChange(); setSignOutError(null);
      navigateBase("/clinician/sign-in");
    } catch (error) { setSignOutError((error as Error).message); }
  };
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  // Bumped whenever the record changes, so every open screen re-reads. Cheaper
  // and more honest than each screen polling on its own.
  const [version, setVersion] = useState(0);
  const [header, setHeader] = useState<PageHeader>(() => defaultHeader(route.segments, workspace));

  const refresh = useCallback(() => setVersion((n) => n + 1), []);
  const capture = useCapture(refresh);
  const dragging = useWindowCapture(capture.enqueue);
  const [wide, menuOpen, setMenuOpen] = useMenu();

  // On a phone the menu lies over the page, so choosing somewhere closes it.
  const go = useCallback(
    (to: string) => {
      if (!wide) setMenuOpen(false);
      navigate(to);
    },
    [wide, navigate, setMenuOpen],
  );

  // The route's own heading, restored on every navigation. A screen that knows
  // better replaces it after its own fetch resolves — which is why this cannot
  // be derived during render: it would win the race back and flicker.
  useEffect(() => {
    setHeader(defaultHeader(route.segments, workspace));
  }, [route.path, workspace]);

  useEffect(() => {
    let live = true;
    const poll = () =>
      api
        .health()
        .then((result) => {
          if (!live) return;
          rememberInstallation(result.packaged);
          setHealth(result);
          setHealthError(null);
        })
        .catch((exc: Error) => live && setHealthError(exc.message));
    poll();
    const timer = window.setInterval(poll, HEALTH_INTERVAL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [version]);

  const segments = route.segments[0] === "clinician" && route.segments.length > 1 ? route.segments.slice(1) : route.segments;
  const [first, second, ...rest] = segments;
  const tail = [second, ...rest].filter(Boolean).join("/");

  let screen: React.ReactNode;
  if ((needsClinician && !checked) || (session && workspace === "clinician" && clinicianPath(route.path) !== route.path)) {
    screen = <p role="status">Checking clinician session…</p>;
  } else if (signingIn || (needsClinician && !session)) {
    screen = <ClinicianLogin onSignedIn={result => { setSession(result); navigateBase("/clinician"); }} />;
  } else if (!first || first === "overview") {
    screen = <><SharedCare navigate={navigate} compact/>{createElement("health-overview", { key: version })}</>;
  } else if (first === "clinician" && !tail) {
    screen = <ClinicianHome key={session?.clinician.username} navigate={navigate} />;
  } else if (first === "issue" && tail) {
    screen = <ClinicianHome key={session?.clinician.username} issueId={tail} navigate={navigate} />;
  } else if (first === "observation" && tail) {
    screen = <OriginalObservation id={tail} clinician={workspace === "clinician"} />;
  } else if (first === "notifications") {
    screen = <Notifications navigate={navigate}/>;
  } else if (first === "care-team") {
    screen = <CareTeam version={version} />;
  } else if (first === "profile") {
    screen = createElement("patient-profile");
  } else if (first === "documents") {
    screen = <MedicalDocuments clinician={workspace === "clinician"} navigate={navigate} version={version}/>;
  } else if (first === "ask") {
    screen = (
      <Ask
        navigate={navigate}
        onBoxState={refresh}
        documents={health?.record.artifacts ?? null}
      />
    );
  } else if (first === "calendar") {
    screen = createElement("health-calendar", { key: version });
  } else if (first === "timeline") {
    screen = <>{workspace === "patient-carer"?<PatientIdentity version={version} clinician={false}/>:null}<ClinicalTimeline key={workspace} navigate={navigate} version={version} clinician={workspace === "clinician"}/></>;
  } else if (first === "record" && tail) {
    screen = (
      <Entity
        id={decodeURIComponent(tail)}
        navigate={navigate}
        version={version}
        setHeader={setHeader}
      />
    );
  } else if (first === "record") {
    screen = workspace === "clinician" ? <ClinicalRecord navigate={navigate} version={version} onChanged={refresh}/> : <Record navigate={navigate} version={version} />;
  } else if (first === "artifact" && tail) {
    screen = <Artifact short={tail} version={version} setHeader={setHeader} />;
  } else if (first === "files") {
    screen = (
      <Files
        path={tail ? tail.split("/").map(decodeURIComponent).join("/") : ""}
        navigate={navigate}
        version={version}
        onChanged={refresh}
        setHeader={setHeader}
      />
    );
  } else if (first === "add") {
    screen = (
      <>{workspace === "patient-carer"?<PatientIdentity version={version} clinician={false}/>:null}<CapturePanel capture={capture} onCaptured={refresh} navigate={navigate} />{workspace === "patient-carer"?<SubmittedUpdates version={version} onChanged={refresh}/>:null}</>
    );
  } else if (first === "summary") {
    screen = (
      <SummaryScreen
        id={tail ? decodeURIComponent(tail) : undefined}
        version={version}
        navigate={navigate}
        onChanged={refresh}
        setHeader={setHeader}
      />
    );
  } else if ((first === "progress-notes" || first === "doctor-notes")) {
    screen = <DoctorNotes key={session?.clinician.username} version={version} />;
  } else if (first === "review") {
    screen = <Review version={version} onChanged={refresh} navigate={navigate} />;
  } else if (first === "welcome") {
    screen = <Welcome navigate={navigate} onChanged={refresh} />;
  } else if (first === "settings") {
    screen = <Settings health={health} navigate={navigate} version={version} onChanged={refresh} setHeader={setHeader} />;
  } else {
    screen = (
      <p>
        There is no page at <code className="font-mono">{route.path}</code>.{" "}
        <Link to="/" navigate={navigate}>
          Back to the start
        </Link>
        .
      </p>
    );
  }

  const asking = first === "ask";

  return (
    <ClinicianLanguage.Provider value={workspace === "clinician" && Boolean(session)}>
    <ClinicianComparisonProvider key={session?.clinician.username ?? "patient"}>
    <div className="app-shell" data-workspace={workspace} onClickCapture={event => {
      if (workspace !== "clinician" || !session) return;
      const anchor = (event.target as Element).closest("a");
      const href = anchor?.getAttribute("href");
      if (!anchor || !href || !href.startsWith("/")) return;
      const next = clinicianPath(href);
      if (next !== href) {
        anchor.setAttribute("href", next);
        if (!event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) { event.preventDefault(); navigate(next); }
      }
    }}>
      {dragging ? <DropOverlay /> : null}

      {menuOpen && (!needsClinician || session) ? (
        wide ? (
          <div className="no-print sticky top-0 h-screen shrink-0 border-r border-[color:var(--color-rule)]">
            <Sidebar
              workspace={workspace}
              health={health}
              path={route.path.startsWith("/clinician/") ? route.path.slice(10) : route.path}
              navigate={go}
              uploading={capture.uploading}
            />
          </div>
        ) : (
          <div className="no-print fixed inset-0 z-40 flex">
            <div className="h-full">
              <Sidebar
                workspace={workspace}
                health={health}
                path={route.path.startsWith("/clinician/") ? route.path.slice(10) : route.path}
                navigate={go}
                uploading={capture.uploading}
                onClose={() => setMenuOpen(false)}
              />
            </div>
            {/* The page behind, dimmed; tapping it closes the menu. */}
            <button
              type="button"
              aria-label="Close the menu"
              className="h-full flex-1 cursor-default bg-[color:var(--color-ink)]/40"
              onClick={() => setMenuOpen(false)}
            />
          </div>
        )
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="app-topbar">
        <header className="app-header no-print">
          {(menuOpen && wide) || (needsClinician && !session) ? null : (
            <button
              type="button"
              className="icon-btn relative"
              onClick={() => setMenuOpen(true)}
              aria-label="Open the menu"
              aria-controls="menu"
              aria-expanded={menuOpen}
            >
              <MenuIcon />
              {/* With the menu closed, the one count that matters stays in
                  view. A number, said again in words for a screen reader. */}
              {health && health.review.total > 0 ? (
                <span className="absolute -top-1.5 -right-2.5 min-w-[1.25rem] rounded-full border-2 border-[color:var(--color-paper)] bg-[color:var(--color-accent)] px-1 text-center leading-5 text-[color:var(--color-paper)]">
                  {health.review.total}
                  <span className="sr-only"> waiting for you</span>
                </span>
              ) : null}
            </button>
          )}
          <div className="app-heading">
            {first === "clinician" && session ? <p className="page-title">Review dashboard</p> : <h1 className="page-title">
              {asking ? workspace === "clinician" ? "Ask the patient record" : "Your health record" : header.title}
            </h1>}
            {asking ? null : header.badge}
            {/* A label, not a banner: whoever opened a demonstration knows it
                is one, but a printed page or a screenshot must still say so. */}
            {health?.vault.demo ? (
              <span
                className="badge demo-indicator"
                title="Everything in this record was invented so the screens have something to show."
              >
                Fictional demo
              </span>
            ) : null}
          </div>

          {workspace === "clinician" && session ? <div className="clinician-account">
            <div><span>{session.clinician.name}</span><span className="metadata dashboard-line">{session.clinician.role}</span></div>
            <button className="btn" onClick={signOut}>Sign out</button>
          </div> : null}
          {signOutError ? <p role="alert">{signOutError}</p> : null}
          <label className="workspace-switch">
            <span className="switch-caption"><span>Workspace</span>{health?.vault.demo ? <span className="switch-demo-indicator">Fictional demo</span> : null}</span>
            <select
              value={workspace}
              title="Demonstration only. Does not sign you in or unlock clinical notes."
              onChange={(event) => go(event.target.value === "clinician" ? "/clinician" : "/overview")}
            >
              <option value="patient-carer">Patient &amp; carer workspace</option>
              <option value="clinician">Clinician workspace</option>
            </select>
          </label>
        </header>
        {workspace === "clinician" && session ? <PatientIdentity key={session.clinician.username} version={version} /> : null}
        </div>

        <div className={`app-content ${asking ? "app-content-narrow" : ""} ${workspace === "clinician" && (first === "clinician" || first === "issue") ? "app-content-review" : ""}`}>
          {(!needsClinician || session) ? <Attention
            health={health}
            error={healthError}
            navigate={go}
            /* The settings screen holds the control for the box and reports
               what stopped in its own words. One telling, not two. */
            quietEndpoint={first === "add" || first === "settings" || first === "welcome" || first === "notifications" || first === "documents" || first === "artifact" || first === "timeline" || (workspace === "clinician" && first === "record") || first === "progress-notes" || first === "doctor-notes" || (workspace === "clinician" && (first === "clinician" || first === "issue"))}
            /* The waiting screen is the list those two notices point at. */
            quietReview={first === "review" || workspace === "patient-carer"}
          /> : null}

          {asking || !header.subtitle ? null : (
            <p className="mt-2 mb-5 max-w-2xl text-[color:var(--color-muted)]">
              {header.subtitle}
            </p>
          )}

          {/*
            Per screen, and reset by the route: a screen that cannot draw itself
            must not blank the menu, the heading and every other screen with it.
            The built bundle is served from disk while the server holds its own
            version in memory, so "the page is newer than the program answering
            it" is an ordinary state here, not a rare one.
          */}
          <main className="flex flex-1 flex-col">
            <Boundary key={session?.clinician.username ?? "signed-out"} resetKey={route.path}>{screen}</Boundary>
          </main>


        </div>
      </div>
    </div>
    </ClinicianComparisonProvider>
    </ClinicianLanguage.Provider>
  );
}

/**
 * Whether the menu is open, and whether the screen is wide enough for it to sit
 * beside the page.
 *
 * The desktop sidebar is persistent. Narrow screens start with a collapsed
 * menu and close it after navigation or Escape.
 */
function useMenu(): [boolean, boolean, (open: boolean) => void] {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const query = window.matchMedia(WIDE);
    const onChange = () => {
      setWide(query.matches);
      setOpen(false);
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (!open || wide) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, wide]);

  return [wide, wide || open, setOpen];
}

/**
 * The heading for a route the shell can answer on its own.
 *
 * An entity and an artefact are deliberately vague here — they are replaced the
 * moment the screen below knows the name — and vague is the right failure: a
 * page that briefly says "One entry in your record" and then says "Perindopril"
 * has never said anything untrue.
 */
function defaultHeader(segments: string[], workspace: "clinician" | "patient-carer"): PageHeader {
  if (segments[0] === "clinician" && segments[1] === "sign-in") return { title: "Clinician sign-in", subtitle: "" };
  if (segments[0] === "clinician" && segments.length > 1) segments = segments.slice(1);
  const [first, second] = segments;
  if ((first === "clinician" && !second) || first === "issue") {
    return { title: "Review dashboard", subtitle: "" };
  }
  if (!first || first === "overview") {
    return { title: "Home", subtitle: "" };
  }
  if (first === "care-team") return { title: workspace === "clinician" ? "Care team" : "My care team", subtitle: "" };
  if (first === "observation") return { title: "Original typed observation", subtitle: "" };
  if (first === "profile") {
    return { title: workspace === "clinician" ? "Patient profile" : "My profile", subtitle: "" };
  }
  if (first === "notifications") return {title:"Notifications",subtitle:""};
  if (first === "documents") {
    return { title: "Documents", subtitle: "" };
  }
  if (first === "calendar") return { title: "Calendar", subtitle: "Appointments and important dates." };
  if (first === "timeline") {
    return {
      title: "Timeline",
      subtitle: second
        ? "Everything the record holds about one entry, newest first."
        : workspace === "clinician" ? "Letters, updates and care events in date order." : "Your updates and shared care documents in date order.",
    };
  }
  if (first === "record" && second) {
    return { title: workspace === "clinician" ? "Patient record entry" : "One entry in your record", subtitle: "Reading…" };
  }
  if (first === "record") {
    return {
      title: workspace === "clinician" ? "Clinical record" : "My health record",
      subtitle: workspace === "clinician" ? "" :
        "Your medications, allergies, health problems and the people who look after you — put together from your own documents, and only from what you have confirmed.",
    };
  }
  if (first === "artifact") {
    return { title: workspace === "clinician" ? "Patient source document" : "One of your documents", subtitle: "Reading…" };
  }
  if (first === "files") {
    return { title: workspace === "clinician" ? "Record files" : "Your folder", subtitle: "Reading…" };
  }
  if (first === "welcome") {
    return {
      title: "Your record is ready",
      subtitle: "One more question, then it is yours to use. Everything else is in Settings.",
    };
  }
  if (first === "ask") {
    return { title: workspace === "clinician" ? "Ask the patient record" : "Ask your record", subtitle: "" };
  }
  if ((first === "progress-notes" || first === "doctor-notes")) {
    return {
      title: "Progress notes",
      subtitle: "",
    };
  }
  if (first === "review") {
    return {
      title: "Confirm extracted information",
      subtitle:
        workspace === "clinician" ? "Information extracted from Jane’s documents that awaits confirmation." : "Things read from your documents that need a quick yes or no from you. Nothing important joins your record until you say so.",
    };
  }
  if (first === "summary") {
    return second
      ? { title: workspace === "clinician" ? "Patient appointment summary" : "Your sheet for this appointment", subtitle: "Reading…" }
      : {
          title: "Appointment summary",
          subtitle:
            workspace === "clinician" ? "Prepare a source-linked summary of Jane’s changes, medications, allergies and questions for the appointment." : "One page to take with you: what changed, what you take, what you are allergic to, and the question you came to ask. Every line says which of your documents it came from.",
        };
  }
  if (first === "settings") {
    return {
      title: "Settings",
      subtitle:
        "Where your record is kept, which computer reads it, and what this app watches out for.",
    };
  }
  if (first === "add") {
    return {
      title: workspace === "clinician" ? "Upload or record information" : "Record an update",
      subtitle:
        "",
    };
  }
  return { title: "Not found", subtitle: "" };
}
