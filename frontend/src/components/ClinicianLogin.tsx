import { useState } from "react";
import { authRequest, notifyAccountChange } from "../clinicianAuth";
import type { ClinicianSession } from "../types";
export function ClinicianLogin({ onSignedIn }: { onSignedIn: (session: ClinicianSession) => void }) {
  const [username,setUsername]=useState("");
  const [password,setPassword]=useState("");
  const [error,setError]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const signIn=async(event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const session=await authRequest("login",{username,password});
      setPassword(""); notifyAccountChange(); onSignedIn(session);
    } catch(err) { setError((err as Error).message); setPassword(""); }
    finally { setBusy(false); }
  };
  return <section className="surface clinician-login" aria-labelledby="clinician-sign-in">
    <h2 id="clinician-sign-in" className="section-heading">Clinician sign-in</h2>
    <p className="metadata">Use your clinician account to open the review dashboard.</p>
    <form onSubmit={signIn} aria-busy={busy}>
      <label>Username<input className="field" autoComplete="username" required value={username} onChange={e=>setUsername(e.target.value)} aria-describedby={error ? "login-error":undefined} /></label>
      <label>Password<input className="field" type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)} aria-describedby={error ? "login-error":undefined} /></label>
      {error ? <p id="login-error" role="alert" className="login-error">{error}</p>:null}
      <button className="btn btn-primary" disabled={busy}>{busy ? "Signing in…":"Sign in"}</button>
    </form>
    <p className="metadata">Signing in does not unlock passphrase-protected clinical notes.</p>
  </section>;
}
