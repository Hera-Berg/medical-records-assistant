"""Recoverable replacement of Jane's demo only. All new sources are authored fiction."""
from pathlib import Path
import json
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from .. import profile, care_team, ingest, projection, files
from ..events import envelope
from ..vault import Vault, scaffold
from ..demo_metadata import SCENARIO
from ..server.state import RecordState
from .jane import source_pdf, STATIN
from .authoring import claim, confirm

VERSION='jane-review-v2'

def source(id, date, title, author, role, text, *, received=None, **extra):
    return dict(id=id,date=date,document_date=extra.pop('document_date',date),received=received or date,title=title,author=author,role=role,text=text,**extra)

SOURCES=[
 source('baseline','2024-11-01','Baseline health and usual function','Dr James Smith','Usual GP',
  'Jane usually walks to communal meals using her frame and eats independently. She enjoys gardening and family visits. Hypertension and knee osteoarthritis are recorded; original diagnosis dates are unknown. Penicillin rash was reported by Jane years ago; timing and severity are uncertain.'),
 source('nurse-history','2024-12-05','Nursing review of usual daily activities','Morgan Bell','Registered nurse',
  'Jane walks to meals with her frame and participates in the dining room. Staff assist with showering. No recent change in participation is reported.'),
 source('bennett-consult','2025-03-26','Completed locum GP consultation','Dr Olivia Bennett','Locum GP',
  'Completed consultation at 09:30 Australia/Sydney. Jane described her usual mobility with a frame. Hypertension and knee osteoarthritis were reviewed. No medication change was issued.',consultation=True),
 source('cardio-diagnosis','2025-05-02','Cardiology assessment: atrial fibrillation','Dr Matthew Thompson','Cardiologist',
  'Atrial fibrillation documented. Apixaban 5 mg twice daily and metoprolol 25 mg twice daily were recommended for GP prescribing and reconciliation. No INR-monitoring plan is documented.'),
 source('gp-af','2025-05-06','GP prescription record for atrial fibrillation','Dr James Smith','Usual GP',
  'GP issued apixaban 5 mg twice daily and metoprolol 25 mg twice daily following the cardiology letter. Amlodipine 2.5 mg once daily continued.'),
 source('previous-pathology','2026-04-14','Previous blood results for comparison','Example Pathology Laboratory','Laboratory',
  'Haemoglobin 128 g/L (reference 115–165); sodium 138 mmol/L (135–145); potassium 4.2 mmol/L (3.5–5.2); creatinine 72 µmol/L (45–90). No laboratory flags.',results=[['Haemoglobin','128','g/L','115–165',''],['Sodium','138','mmol/L','135–145',''],['Potassium','4.2','mmol/L','3.5–5.2',''],['Creatinine','72','µmol/L','45–90','']]),
 source('med-chart','2026-08-28','Current medication chart','Dr James Smith','Usual GP',
  'Current directions: apixaban 5 mg twice daily; metoprolol 25 mg twice daily; amlodipine 2.5 mg once daily; rosuvastatin 5 mg nightly. Amlodipine was reduced from 5 mg to 2.5 mg on this date. No later issued changes are documented.'),
 source('physio','2026-09-10','Physiotherapy assessment','Sarah Green','Physiotherapist',
  'Jane uses her frame for walking. Practice location: Sydney Aged Care · therapy room. Supervised walking practice was recommended. The assessment does not record a new diagnosis.',organisation='Sydney Aged Care'),
 source('cardio-recommendation','2026-09-24','Cardiologist recommends medication review','Dr Matthew Thompson','Cardiologist',
  'Recommend GP review of symptoms and current medicines if dizziness persists. This letter does not issue a dose change or advise stopping apixaban. The recommendation awaits assessment in the current context.',received='2026-10-24',recommendation=True),
 source('smith-consult','2026-10-21','Completed usual GP consultation','Dr James Smith','Usual GP',
  'Completed consultation at 09:30 Australia/Sydney. Jane reported intermittent dizziness. Current chart reviewed: apixaban 5 mg twice daily, metoprolol 25 mg twice daily, amlodipine 2.5 mg daily and rosuvastatin 5 mg nightly. No medication change issued.',consultation=True),
 source('low-haemoglobin','2026-10-27','Haemoglobin lower than previous result','Example Pathology Laboratory','Laboratory',
  'Collected 27 October; report authored 28 October. Haemoglobin 101 g/L (reference 115–165), flagged LOW; previous 128 g/L on 14 April 2026. No critical alert is attached to this report. This report does not establish a cause for reported dizziness.',document_date='2026-10-28',received='2026-10-28',results=[['Haemoglobin','101','g/L','115–165','LOW; previous 128 on 14 Apr 2026']]),
 source('critical-sodium','2026-10-29','Sodium report carries a laboratory critical alert','Example Pathology Laboratory','Laboratory',
  'Collected 29 October; authored and received 30 October. Sodium 122 mmol/L (reference 135–145), flagged LOW. LABORATORY CRITICAL ALERT: sodium below this fictional laboratory’s critical threshold of 125 mmol/L. Previous sodium 138 mmol/L on 14 April 2026. This is a laboratory flag, not a clinician-assigned assessment or a confirmed explanation for symptoms. No clinician response or patient communication has yet been recorded in this demo.',document_date='2026-10-30',received='2026-10-30',laboratory_flag='Laboratory critical flag: sodium below 125 mmol/L',results=[['Sodium','122','mmol/L','135–145','LOW · LABORATORY CRITICAL ALERT; previous 138 on 14 Apr 2026']]),
 source('stable-renal','2026-10-29','Potassium and creatinine within laboratory ranges','Example Pathology Laboratory','Laboratory',
  'Potassium 4.3 mmol/L (3.5–5.2), previous 4.2; creatinine 74 µmol/L (45–90), previous 72 on 14 April 2026. Neither result is flagged. This report contains no sodium or haemoglobin result.',document_date='2026-10-30',received='2026-10-31',results=[['Potassium','4.3','mmol/L','3.5–5.2','previous 4.2'],['Creatinine','74','µmol/L','45–90','previous 72']]),
 source('nurse-clinical','2026-10-31','Nursing record of current support','Morgan Bell','Registered nurse',
  'Jane requested staff remain nearby while she walks with her frame. Jane said she still worries about dizziness. No fall was observed during this recorded walk. Staff recorded the request; no cause is assigned.'),
 source('patient-dizzy','2026-10-23','Jane reports persistent dizziness','Jane Doe','Patient',
  'I still feel dizzy at times when I get up. I would like to talk about it at my next appointment.',submission=True,issue='dizziness'),
 source('carer-walk','2026-10-24','Walking support requested','Emily Doe','Daughter / authorised demo carer',
  'On our walk to the dining room Jane asked me to stay beside her and paused to rest. This is what I saw on our walk, not a report from staff.',submission=True,issue='walking'),
 source('staff-walk','2026-10-25','Walking support requested','Morgan Bell','Registered nurse',
  'Jane asked me to stay nearby while she walked with her frame towards the dining room. This contribution records her request to staff.',submission=True,issue='walking'),
 source('meals','2026-10-26','Jane reports fewer communal breakfasts','Jane Doe','Patient',
  'I am staying in my room for breakfast because I worry about feeling dizzy. I miss talking to the other residents.',submission=True,issue='meals'),
 source('med-question','2026-10-22','Jane asks which heart tablet changed','Jane Doe','Patient',
  'Which heart tablet changed in August? I would like someone to explain my current medication list at my next appointment. This question has not yet been answered in the recorded demo.',submission=True,issue='med-question',unresolved=True),
]
from .pathology_reports import enrich, report_pdf, native_results
SOURCES=[enrich(item) for item in SOURCES]
# The source helper accepts explicit document dates separate from event dates.

def populate(root, values, team, sealed=()):
    root=Path(root)
    if root.exists() and any(root.iterdir()):raise ValueError('Population requires a new empty staging directory.')
    scaffold(root)
    from . import demo_config
    files.write_text((root/'config.toml'),demo_config())
    vault=Vault.open(root)
    files.write_text((root/'DEMO-DATA.md'),'# Fictional Jane review demonstration\nAll sources are newly authored fictional demo material, not recovered historical originals.\n')
    vault.append(envelope.new('note.recorded',vault.identity.id,payload={'review_dataset':VERSION,'fictional':True}))
    values=dict(values);values['date_of_birth']='1935-11-01'
    values['important_notes']='Fictional demonstration. Atrial fibrillation, hypertension and knee osteoarthritis. Current directions are in the 28 August 2026 chart. Recent reports have not yet been clinically reviewed. No symptom cause is inferred.'
    vault.append(envelope.new(profile.UPDATED,vault.identity.id,payload={'values':values,'demo_scenario':SCENARIO}))
    vault.append(envelope.new('note.recorded',vault.identity.id,payload={'demo_scenario':SCENARIO,'care_team':team,'fictional':True}))
    # Restore unchanged sealed historical envelopes into their original device shards.
    # This is backup restoration, not authorship on behalf of another device.
    for e in sealed:
        shard=root/'events'/(e.ts[:7]+'.'+e.device+'.jsonl')
        with shard.open('a') as stream:stream.write(json.dumps(e.to_dict(),ensure_ascii=False)+'\n')
    manifest=[];refs={}
    for item in SOURCES:
        docdate=item.get('document_date',item['date'])
        header=f"FICTIONAL DEMONSTRATION SOURCE — {VERSION}\n{item['title']}\nJane Doe | DOB 1 November 1935\nAuthor: {item['author']} | {item['role']}\nEvent: {item['date']} | Authored: {docdate} | Fictional receipt: {item['received']}\n\n"
        text=header+item['text']+'\n\nNewly authored fictional record, not an actual historical report.'
        from .document_templates import authored_source
        organisation=item.get('organisation') or next((m['organisation'] for m in team if m['name']==item['author']), 'Example Pathology Laboratory' if item['role']=='Laboratory' else 'Sydney Aged Care')
        pdf=report_pdf(item,envelope.now_ts()) if item.get('ordered_panel') else authored_source(item,organisation) if not item.get('submission') else source_pdf(text)
        result=ingest.ingest_bytes(vault,pdf,ingest.CaptureContext(source='import',original_filename=item['id']+'.pdf',note='New fictional review demo source'))
        native={'title':item['title'],'date':docdate,'authored_date':docdate,'received_date':item['received'],'organisation':item.get('organisation') or next((m['organisation'] for m in team if m['name']==item['author']), 'Example Pathology Laboratory' if item['role']=='Laboratory' else 'Sydney Aged Care'),
                'specialty':item['role'],'professional':item['author'],'recipient':'Dr James Smith, Sydney Aged Care','patient':{'name':'Jane Doe','dob':'1 November 1935'},
                'sections':[{'heading':'Original fictional source','text':item['text']}],
                'results':[dict(test=r[0],result=r[1],unit=r[2],reference=r[3],flag=r[4]) for r in item.get('results',[])],
                'medications':[],'prescriptions':[],'release':None}
        if not item.get('submission'):native.update(facility_template='facility-documents-v1',original_document_layout=True)
        if item.get('ordered_panel'):
            native.update(ordered_panel=item['ordered_panel'],pathology_report=True,results=native_results(item['results']),sections=[])
            if item.get('laboratory_flag'):native['sections']=[{'heading':'Laboratory alert','text':item['laboratory_flag']}]
        if item.get('consultation'):native['calendar_appointment']={'professional':item['author'],'time':'09:30','status':'Completed','location':'Sydney Aged Care · GP room'}
        p={**item,'review_dataset':VERSION,'demo_scenario':SCENARIO,'demo_item_id':item['id'],'fictional':True,'patient_visible':True,
           'access':'submission' if item.get('submission') else 'clinical','event_date':item['date'],'source_type':'Patient/carer/staff update' if item.get('submission') else 'Clinical document',
           'event_type':'observation' if item.get('submission') else 'appointment' if item.get('consultation') else 'document',
           'artifact':result.short,'native_document':native,'status':'Prepared fictional source; awaiting clinician review'}
        vault.append(envelope.new('note.recorded',vault.identity.id,payload=p));refs[item['id']]=result.short
        manifest.append(dict(id=item['id'],artifact=result.short,event_date=item['date'],document_date=docdate,fictional_received_date=item['received'],uploaded_at=result.event.ts,purpose=item['title'],access=p['access']))
    # Explicit existing fictional care relationships and upcoming bookings, not unresolved actions.
    appointments=[('2026-11-03','09:30','Dr James Smith','GP review'),('2026-11-12','11:00','Sarah Green, physiotherapist','Physiotherapy'),('2026-11-18','14:00','Olivia Reed, audiologist','Hearing-aid review')]
    for date,time,who,title in appointments:
        vault.append(envelope.new('note.recorded',vault.identity.id,payload={'calendar_only':True,'demo_scenario':SCENARIO,'fictional':True,'calendar_id':'demo-v2:'+date,'calendar_entry':{'date':date,'time':time,'title':title,'professional':who,'kind':'appointment','status':'Booked'}}))
    # Explicit permission in this fictional scenario; not inferred from next-of-kin.
    vault.append(envelope.new('note.recorded',vault.identity.id,payload={'review_recipient_authorisation':{'patient':'Jane Doe','carer':'Emily Doe'},'basis':'Fictional Jane explicitly authorises Emily to receive shared documents and appointment requests in this demonstration.'}))
    # Transcribed current chart. These claims are sourced, not AI-generated clinical decisions.
    for name,dose in STATIN.items():
        e=claim(vault.identity.id,'med:'+name.lower(),'dose',dose,artifact=refs['med-chart'],ts=envelope.now_ts(),occurred={'value':'2026-08-28','precision':'day','uncertainty_days':0},subject_name=name)
        e=replace(e,provenance={'model':'authored-fictional-demo','model_rev':'no-model-run','prompt_hash':'not-applicable','artifact':e.provenance['artifact']})
        vault.append(e);vault.append(confirm(vault.identity.id,e.id))
    for name in ('Hypertension','Knee osteoarthritis','Atrial fibrillation'):
        e=claim(vault.identity.id,'problem:'+name.lower().replace(' ','-'),'name',name,artifact=refs['cardio-diagnosis'] if name=='Atrial fibrillation' else refs['baseline'],ts=envelope.now_ts(),subject_name=name)
        e=replace(e,provenance={'model':'authored-fictional-demo','model_rev':'no-model-run','prompt_hash':'not-applicable','artifact':e.provenance['artifact']})
        vault.append(e);vault.append(confirm(vault.identity.id,e.id))
    files.write_text((root/'DATASET-MANIFEST.json'),json.dumps({'fictional':True,'version':VERSION,'sources':manifest},indent=2)+'\n')
    projection.rebuild(vault)
    return vault


def refresh(root):
    root=Path(root).resolve();old=Vault.open(root);snapshot=RecordState(old).snapshot()
    values=profile.read(snapshot.events)['values']
    if not old.is_demo or values.get('full_name')!='Jane Doe':raise ValueError('Only Jane’s existing fictional demo may be refreshed.')
    if any(e.payload.get('review_dataset')==VERSION for e in snapshot.events):raise ValueError('Already refreshed; refusing to reset workflow state.')
    team=care_team.listing(snapshot.events,snapshot.artifacts,values)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    archive=root.parent/'archives'/('jane-before-review-v2-'+stamp)
    archive.parent.mkdir(exist_ok=True)
    shutil.copytree(root,archive)  # Complete originals and event log before any replacement.
    staging=root.with_name(root.name+'-review-staging-'+stamp)
    populate(staging,values,team,[e for e in snapshot.events if e.type.startswith('compartment.')])
    if (root/'config.toml').exists():shutil.copy2(root/'config.toml',staging/'config.toml')
    # Preserve original active directory too, until the replacement is fully built.
    prior=root.with_name(root.name+'-pre-swap-'+stamp)
    root.rename(prior)
    try:staging.rename(root)
    except BaseException:
        prior.rename(root);raise
    print(json.dumps({'active':str(root),'archive':str(archive),'additional_recovery':str(prior),'clinical_documents':14,'updates':5},indent=2))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--vault',required=True)
    refresh(parser.parse_args().vault)
