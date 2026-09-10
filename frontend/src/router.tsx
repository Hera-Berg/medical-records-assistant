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

export function useRoute(): [Route, (to: string) => void] {
  const [route, setRoute] = useState<Route>(current);

  useEffect(() => {
    const onPop = () => setRoute(current());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((to: string) => {
    if (to === window.location.pathname) return;
    window.history.pushState(null, "", to);
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
}: {
  to: string;
  navigate: (to: string) => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <a
      href={to}
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
