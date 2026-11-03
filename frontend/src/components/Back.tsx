/**
 * The way back, at the top of every page that is not one of the main tabs.
 *
 * A fixed destination where the page has one parent (everything under More
 * goes back to More), and the browser's own Back where a page is reached from
 * many places (a document, a note) — falling back to a named page when this
 * app has no step of its own to undo, so it never leaves the record.
 */

import { canGoBack } from "../router";
import { ChevronLeft } from "./icons";

export function Back({
  to,
  label,
  navigate,
  previous = false,
  className = "",
}: {
  to: string;
  label: string;
  navigate: (to: string) => void;
  /** Go back to wherever the person came from, if that was in this app. */
  previous?: boolean;
  className?: string;
}) {
  const useHistory = previous && canGoBack();
  return (
    <a
      href={to}
      className={`pt-back no-print ${className}`}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        if (useHistory) window.history.back();
        else navigate(to);
      }}
    >
      <ChevronLeft />
      {useHistory ? "Back" : label}
    </a>
  );
}

/** Where a page's back link goes, for pages that do not draw their own. */
export function backFor(first: string | undefined): { to: string; label: string; previous?: boolean; phoneOnly?: boolean } | null {
  switch (first) {
    case "documents":
    case "care-team":
    case "notifications":
    case "profile":
    case "settings":
    case "files":
    case "review":
    case "summary":
      return { to: "/more", label: "More" };
    case "calendar":
      // A tab of its own on a wide screen; under More on a phone.
      return { to: "/more", label: "More", phoneOnly: true };
    case "artifact":
    case "observation":
      return { to: "/timeline", label: "History", previous: true };
    case "add":
      return { to: "/", label: "Home", previous: true };
    default:
      return null;
  }
}
