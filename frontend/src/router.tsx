/**
 * Four screens and a hand-rolled router.
 *
 * A routing library would be one more dependency to vendor into a bundle that
 * must contain everything it needs, for a surface of four paths. This is the
 * History API and one event listener.
 */

import { useCallback, useEffect, useState } from "react";

export interface Route {
  path: string;
  segments: string[];
}

function current(): Route {
  const path = window.location.pathname || "/";
  return { path, segments: path.split("/").filter(Boolean) };
}

/**
 * How many in-app steps can be undone with the browser's own Back. Counted
 * here rather than read from `history.length`, which also counts pages from
 * before this app was opened — a Back link must never leave the record.
 */
let depth = 0;

export function canGoBack(): boolean {
  return depth > 0;
}

export function useRoute(): [Route, (to: string) => void] {
  const [route, setRoute] = useState<Route>(current);

  useEffect(() => {
    const onPop = () => {
      depth = Math.max(0, depth - 1);
      setRoute(current());
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((to: string) => {
    if (to === window.location.pathname + window.location.search + window.location.hash) return;
    window.history.pushState(null, "", to);
    depth += 1;
    setRoute(current());
    window.scrollTo(0, 0);
  }, []);

  return [route, navigate];
}

/**
 * An internal link that does not reload the page.
 *
 * Still a real `<a href>`: middle-click, open-in-new-tab and "copy link" all
 * have to work, and a record whose links cannot be copied is a record whose
 * citations cannot be shared with the person who needs them.
 */
export function Link({
  to,
  navigate,
  children,
  className,
  ariaCurrent,
}: {
  to: string;
  navigate: (to: string) => void;
  children: React.ReactNode;
  className?: string;
  ariaCurrent?: "page";
}) {
  return (
    <a
      href={to}
      aria-current={ariaCurrent}
      className={className}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}
