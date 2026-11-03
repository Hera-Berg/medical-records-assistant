import { createContext, useContext, useState } from "react";
const ComparisonContext = createContext({ chosen: "", setChosen: (_value: string) => {}, expanded:false, setExpanded:(_value:boolean)=>{}, selected:"", setSelected:(_value:string)=>{}, tab:"needs_review", setTab:(_value:string)=>{}, scroll:0, setScroll:(_value:number)=>{} });
/** Per-account, in-memory preference only; the server resolves/validates the period. */
export function ClinicianComparisonProvider({children}: {children:React.ReactNode}) {
  const [chosen,setChosen]=useState("");
  const [expanded,setExpanded]=useState(false);
  const [scroll,setScroll]=useState(0);
  const [selected,setSelected]=useState("");
  const [tab,setTab]=useState("needs_review");
  return <ComparisonContext.Provider value={{chosen,setChosen,expanded,setExpanded,scroll,setScroll,tab,setTab,selected,setSelected}}>{children}</ComparisonContext.Provider>;
}
export function useComparison() { return useContext(ComparisonContext); }
