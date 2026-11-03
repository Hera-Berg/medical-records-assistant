import { createContext, useContext } from "react";
export const ClinicianLanguage = createContext(false);
/** Interface wording only; never transform source document text. */
export function useClinicianLanguage() { return useContext(ClinicianLanguage); }
export function AudienceText({ patient, clinician }: { patient: string; clinician: string }) {
  return <>{useClinicianLanguage() ? clinician : patient}</>;
}
