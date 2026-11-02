import { useCallback, useEffect, useState, useRef } from "react";
import type { ClinicianSession } from "./types";

// Memory only. The opaque authentication cookie is HttpOnly and never read here.
let csrf: string | null = null;
export function clinicianCsrf() { return csrf; }
export async function authRequest(path: string, body?: unknown): Promise<ClinicianSession> {
  const response = await fetch(`/api/auth/clinician/${path}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store",
    headers: {"Content-Type":"application/json", ...(csrf ? {"X-Clinician-CSRF":csrf} : {})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail ?? "Unable to sign in.");
  if (path !== "session") csrf = data.csrf ?? null;
  return data;
}
export function useClinicianSession(path: string) {
  const [session, setSessionState] = useState<ClinicianSession | null>(null);
  const generation = useRef(0);
  const setSession = useCallback((value: ClinicianSession | null) => {
    generation.current++; csrf = value?.csrf ?? null; setSessionState(value); setChecked(true);
  }, []);
  const [checked, setChecked] = useState(false);
  const refresh = useCallback(() => {
    const request = ++generation.current;
    authRequest("session").then(result => {
      if (request === generation.current) { csrf=result.csrf; setSessionState(result); }
    }).catch(() => { if (request === generation.current) { csrf=null; setSessionState(null); } })
      .finally(() => { if (request === generation.current) setChecked(true); });
  }, []);
  useEffect(() => { refresh(); }, [path, refresh]);
  useEffect(() => {
    const onFocus = () => { setSessionState(null); setChecked(false); refresh(); };
    const timer = window.setInterval(refresh, 60_000);
    window.addEventListener("focus", onFocus);
    // A sign-out/account change in another tab clears in-memory protected views.
    const onStorage = (event: StorageEvent) => {
      if (event.key === "health-agent.account-change") { setSession(null); setChecked(false); refresh(); }
    };
    window.addEventListener("storage", onStorage);
    const onExpired = () => { csrf=null; setSession(null); setChecked(true); };
    window.addEventListener("clinician-session-expired", onExpired);
    return () => { window.clearInterval(timer); window.removeEventListener("focus",onFocus); window.removeEventListener("storage",onStorage); window.removeEventListener("clinician-session-expired",onExpired); };
  }, [refresh, setSession]);
  return {session, checked, setSession};
}
export function notifyAccountChange() {
  try { localStorage.setItem("health-agent.account-change", String(Date.now())); } catch { /* Cookie enforcement still applies. */ }
}
export function clinicianPath(to: string) {
  if (/^\/(record|artifact|documents|timeline|calendar|profile|progress-notes|doctor-notes)(\/|$)/.test(to)) return `/clinician${to}`;
  return to;
}
