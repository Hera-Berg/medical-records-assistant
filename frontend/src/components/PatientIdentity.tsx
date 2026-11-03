import { useEffect, useState } from "react";
import { api } from "../api";
export function PatientIdentity({ version, clinician=true }: { version: number;clinician?:boolean }) {
  const [values, setValues] = useState<Record<string,string> | null>(null);
  const [error,setError]=useState(false);
  useEffect(()=>{
    let live=true;
    (clinician?api.clinicianPatient():api.profile()).then(data=>{if(live){setValues(data.values);setError(false);}}).catch(()=>{if(live)setError(true);});
    return ()=>{live=false;};
  },[version,clinician]);
  return <section className="patient-identity-strip" aria-label="Patient identity">
    {error ? <span role="status">Patient identity could not be loaded.</span>:values ? <>
      <strong>{values.full_name}</strong>
      <span><span className="metadata">DOB </span>{values.date_of_birth ? new Date(`${values.date_of_birth}T12:00:00`).toLocaleDateString("en-AU",{day:"numeric",month:"short",year:"numeric"}):"Not recorded"}</span>
      {values.address ? <span>{values.address.split("\n")[0]}</span>:null}
    </>:<span role="status">Loading patient identity…</span>}
  </section>;
}
