import { useEffect, useState } from "react";

export type Workspace = "patient-carer" | "clinician";
const KEY = "health-agent.demo-workspace";

// Presentation preference only. Never used to authorise API requests or unlock notes.
export function useWorkspace(path: string): Workspace {
  const [remembered, setRemembered] = useState<Workspace>(() => {
    try {
      return window.localStorage.getItem(KEY) === "clinician" ? "clinician" : "patient-carer";
    } catch {
      return "patient-carer";
    }
  });
  const workspace = path === "/clinician" || path === "/clinician/"
    ? "clinician"
    : path === "/" || path === "/overview" || path === "/overview/"
      ? "patient-carer"
      : remembered;

  useEffect(() => {
    setRemembered(workspace);
    try {
      window.localStorage.setItem(KEY, workspace);
    } catch {
      // Navigation still works when browser storage is unavailable.
    }
  }, [workspace]);
  return workspace;
}
