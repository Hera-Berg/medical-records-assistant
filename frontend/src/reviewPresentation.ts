import type { ReviewIssue } from './types';
/** Display names and labels derived from evidence; never modifies the source or triages. */
export function reviewTitle(issue:ReviewIssue):string {
  const panel=issue.contributions.find(c=>c.ordered_panel)?.ordered_panel;
  if(panel)return panel;
  const tests=[...new Set(issue.contributions.flatMap(c=>(c.results??[]).map(r=>r.test)))];
  if(tests.length)return tests.join(' & ');
  const letter=issue.contributions.find(c=>['Cardiologist','Audiologist','Physiotherapist','Podiatrist'].includes(c.role));
  if(letter)return `${letter.author} - ${letter.role} letter`;
  if(['walking','walking-support'].includes(issue.id))return 'Walking support';
  if(issue.id==='dizziness')return 'Persistent dizziness';
  if(issue.id==='med-question')return 'Medication question';
  return issue.headline;
}
export function isAbnormal(issue:ReviewIssue):boolean {
  return Boolean(issue.laboratory_flags?.length || issue.contributions.some(c=>c.results?.some(r=>/\b(low|high|abnormal|critical)\b/i.test(r.flag))));
}
export function reviewKind(issue:ReviewIssue):string {
  if(issue.contributions.some(c=>c.results?.length))return 'Pathology';
  if(issue.section==='reported')return issue.contributions.some(c=>c.role!=='Patient')?'Care update':'Patient update';
  return issue.contributions.some(c=>c.role==='Cardiologist')?'Specialist':'Document';
}
export function reviewSummary(issue:ReviewIssue):string {
  const results=issue.contributions.flatMap(c=>c.results??[]);
  if(results.length)return results.filter(r=>/\b(low|high|critical|abnormal)\b/i.test(r.flag)).map(r=>`${r.test} ${r.result} ${r.unit} (laboratory range ${r.reference})`).slice(0,2).join('; ') || 'Results are within the reference intervals supplied by the laboratory.';
  const original=issue.contributions[0]?.original_text?.trim();
  const sentence=original?.split(/(?<=[.!?])\s+(?=[A-Z])/)[0];
  if(sentence&&sentence!==reviewTitle(issue)&&sentence.length<=240)return sentence;
  return issue.headline!==reviewTitle(issue)?issue.headline:'';
}
