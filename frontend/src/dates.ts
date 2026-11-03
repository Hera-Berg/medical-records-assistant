export const SYDNEY = "Australia/Sydney";
export const sydneyDate = (day: string) => new Date(`${day.slice(0,10)}T00:00:00Z`).toLocaleDateString("en-AU",{timeZone:SYDNEY,day:"numeric",month:"short",year:"numeric"});
export const sydneyTimestamp = (stamp: string) => new Date(stamp).toLocaleString("en-AU",{timeZone:SYDNEY,day:"numeric",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit",timeZoneName:"short"});
export function sydneyToday() {
  const parts=new Intl.DateTimeFormat("en-CA",{timeZone:SYDNEY,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());
  return ["year","month","day"].map(type=>parts.find(p=>p.type===type)!.value).join("-");
}
