"""Append comprehensive fictional specialist correspondence; never overwrite originals."""
import copy
import json
import textwrap
from pathlib import Path
from datetime import datetime
from .. import ingest, care_team, profile
from ..events import envelope
from ..vault import Vault
from ..demo_metadata import entries, SCENARIO
from ..server.state import RecordState
from ..server.comparison import SYDNEY

EDITION='specialist-correspondence-v1'


def letters(date,old):
    original=old['native_document']
    return [dict(id='cardio-recommendation',author='Dr Matthew Thompson',role='Cardiologist',title='Cardiology review and prescription reconciliation',
      sections=[
       ('Reason for correspondence',f"Records-based correspondence prepared {date} for Dr James Smith. Jane lives at Example Gardens Residential Aged Care and has documented atrial fibrillation, hypertension and knee osteoarthritis. This is a records-based addendum, not a new face-to-face consultation or a newly completed investigation."),
       ('Earlier correspondence retained',f"The original cardiology letter is dated {original['date']} and was recorded as received on {original['received_date']}. Its original wording remains unchanged in the retained PDF: {old['text']}"),
       ('Clinical background and usual function',"Atrial fibrillation was documented on 2 May 2025. The GP issued apixaban and metoprolol on 6 May 2025. Jane's usual function includes walking to communal meals using a frame and eating independently. The dates of onset of hypertension and knee osteoarthritis are not established by these records."),
       ('Prescriptions recorded in the current chart',"The 28 August 2026 medication chart, reviewed at Dr Smith's completed consultation on 21 October 2026, records the following directions. This list is transcribed for reconciliation; this letter does not issue, renew or change a prescription.\nApixaban 5 mg orally twice daily - anticoagulant treatment recorded following the atrial fibrillation assessment.\nMetoprolol 25 mg orally twice daily - recorded in the same GP prescription record.\nAmlodipine 2.5 mg orally once daily - the chart documents reduction from 5 mg on 28 August 2026.\nRosuvastatin 5 mg orally at night - recorded in the current medication chart.\nNo later issued dose change is documented in the sources reviewed. Jane remains prescribed apixaban."),
       ('Recent source information',"Jane reported intermittent dizziness at the 21 October GP consultation and persistent dizziness in her 23 October update. Her 26 October note describes staying in her room for breakfast because of concern about dizziness. Emily's 24 October observation and staff reports describe requests for walking support. These are distinct attributed reports, not a demonstrated cause or diagnosis."),
       ('Investigations available in the record',"The 27 October full blood examination reports haemoglobin 101 g/L (reference 115-165), compared with 128 g/L on 14 April. The 29 October electrolyte panel reports sodium 122 mmol/L (135-145), compared with 138 mmol/L previously, with an explicit laboratory critical alert. Potassium 4.3 mmol/L (3.5-5.2) and creatinine 74 micromol/L (45-90) are within their stated laboratory ranges. These results postdate the September letter and appear only in this newly authored addendum. Their laboratory flags are not a clinician-assigned urgency assessment, and no causal relationship with dizziness is established here."),
       ('Assessment and prescription questions',"The existing cardiology recommendation is for GP review of symptoms and current medicines if dizziness persists. Jane's recorded question asks which heart tablet changed in August. The chart identifies an amlodipine reduction, but this correspondence does not establish that the question has been discussed with Jane, that she understands the change, or that her medication administration record has been reconciled."),
       ('Plan and communication status',"The original recommendation remains available for the treating clinician to assess alongside the new results and source reports. No new medicine is prescribed and no instruction to stop apixaban is issued. Please reconcile the administration record with the current prescriptions and clarify the documented amlodipine change with Jane in plain language, involving Emily with Jane's permission. The record does not yet document a response to the laboratory alert or a discussion of these findings."),
       ('Source records',"Cardiology assessment: 2 May 2025; GP prescription record: 6 May 2025; medication chart: 28 August 2026; original cardiology letter: 24 September 2026; Dr Smith consultation: 21 October 2026; patient/carer/staff updates: 22-31 October 2026; pathology collection/report/receipt dates are preserved in the original reports."),
       ('Sign-off',"Dr Matthew Thompson | Cardiologist\nHarbourside Cardiology\nFictional author attribution for demonstration; no real signature or authenticated clinical approval.")]),
     dict(id='podiatry-correspondence',author='Daniel Brooks',role='Podiatrist',title='Podiatry correspondence - walking support and foot-care review',sections=[
       ('Reason for correspondence',"Fictional record-review correspondence to Dr James Smith concerning Jane's foot-care review and support with walking. This newly authored letter is not evidence that the earlier 28 October calendar booking was attended; its attendance remains unknown in the absence of a completion record."),
       ('Information available',"Jane's baseline describes using a frame to attend communal meals. Knee osteoarthritis is documented, with original onset date unknown. Emily's 24 October report describes Jane requesting support and pausing during a walk. Staff observations also describe requests for someone to remain nearby. No new foot examination findings, wound, infection, sensory loss or vascular test result are supplied by these sources."),
       ('Scope of review',"A future foot-care assessment can document Jane's own concerns, footwear, skin and nail condition, comfort during walking and the support she prefers. No examination measurements or completed treatment are invented in this correspondence. Dizziness and the pathology findings remain separate matters for the treating clinical team; a foot-care cause is not inferred."),
       ('Medication and coordination',"The current chart includes apixaban. This is recorded as context for medication reconciliation before any intervention, not as a direction to withhold treatment. No medicine, dose change or new prescription is issued by this letter. Coordinate the assessment with Jane and the facility team and record actual findings when an assessment occurs."),
       ('Sign-off',"Daniel Brooks | Podiatrist\nExample Community Health\nRecorded review location: Example Gardens - allied health room.\nNew fictional specialist correspondence; no attendance or patient communication is implied.")]),
     dict(id='audiology-correspondence',author='Olivia Reed',role='Audiologist',title='Audiology correspondence - hearing-aid review preparation',sections=[
       ('Reason for correspondence',"New fictional record-review letter to Dr James Smith ahead of Jane's existing hearing-aid review appointment on 18 November 2026. This correspondence does not create a second appointment or indicate that the future review has already occurred."),
       ('Hearing and communication background',"Jane's existing profile records hearing aids fitted on 3 September 2025, a preference for plain-language explanations and written medication-change information, and a need for extra time to answer. No new audiogram, hearing threshold, otoscopy finding or device-setting change is available in this record."),
       ('Preparation for the booked review',"The planned review can address Jane's experience with her hearing aids and document any concerns she identifies, device use and the findings of an actual assessment. Invite Jane to describe any communication difficulties in her own words. Include Emily where Jane has authorised her involvement. No new diagnosis of hearing deterioration is made in this letter."),
       ('Clinical coordination',"Jane's medication question and the recent patient/carer/staff observations remain attributed to their original sources. Hearing-aid use does not establish why Jane has reported dizziness or fewer communal breakfasts. No vestibular diagnosis, prescription change or completed hearing-aid adjustment is asserted. Review outcomes should be recorded after the appointment."),
       ('Sign-off',"Olivia Reed | Audiologist\nExample Community Audiology\nRecorded location: 2 Demonstration Road, Sydney.\nNew fictional specialist correspondence; the booked appointment remains separate from this letter.")])]


def letter_pdf(item,date,prepared,organisation,location):
    """Multi-page A4 letter with readable text, section hierarchy and page footers."""
    def plain(value):return str(value).replace('–','-').replace('—','-').replace('’',"'").replace('µ','micro').replace('·',' | ')
    def esc(value):return plain(value).replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
    pages=[];commands=[];y=0
    def text(x,y,value,size=10,bold=False):commands.append(f'BT /{"F2" if bold else "F1"} {size} Tf 1 0 0 1 {x} {y} Tm ({esc(value)}) Tj ET')
    def line(y):commands.append(f'0.72 G 0.5 w 44 {y} m 551 {y} l S 0 G')
    def header():
        nonlocal y,commands
        commands=[]
        text(44,797,organisation.upper(),16,True);text(44,777,item['author']+' | '+item['role'],10)
        text(44,760,'FICTIONAL SPECIALIST CORRESPONDENCE - DEMONSTRATION ONLY',8,True);line(748)
        text(44,727,'Jane Doe | DOB: 1 November 1935',11,True)
        text(44,710,'Example Gardens Residential Aged Care',10)
        text(44,691,'To: Dr James Smith | Sydney Aged Care',10);text(390,691,'Letter date: '+date,9)
        y=667
    def finish():
        line(65);text(44,50,'Fictional patient, author and content. Prepared '+prepared[:10]+'. No real clinical signature.',8)
        text(500,36,'Page '+str(len(pages)+1),8)
        pages.append('\n'.join(commands).encode('cp1252'))
    def ensure(height):
        if y-height<86:finish();header()
    header()
    for value in textwrap.wrap(item['title'],width=68):text(44,y,value,13,True);y-=18
    y-=7
    if location:
        for value in textwrap.wrap(location,width=93):text(44,y,value,9);y-=13
        y-=8
    for heading,body in item['sections']:
        ensure(62);text(44,y,heading.upper(),10,True);y-=18
        for paragraph in body.split('\n'):
            for value in textwrap.wrap(plain(paragraph),width=96):
                ensure(15);text(44,y,value,10);y-=14
            y-=5
        y-=9
    finish()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>']
    kids=[]
    for stream in pages:
        number=len(objects)+1;kids.append(f'{number} 0 R')
        objects.extend([f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {number+1} 0 R >>'.encode(),b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream'])
    objects[1]=f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {len(pages)} >>'.encode()
    out=bytearray(b'%PDF-1.4\n');offsets=[]
    for n,obj in enumerate(objects,1):offsets.append(len(out));out+=f'{n} 0 obj\n'.encode()+obj+b'\nendobj\n'
    at=len(out);out+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    for offset in offsets:out+=f'{offset:010d} 00000 n \n'.encode()
    out+=f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{at}\n%%EOF\n'.encode()
    return bytes(out)


def upgrade(root):
    vault=root if isinstance(root,Vault) else Vault.open(Path(root))
    snapshot=RecordState(vault).snapshot();current=entries(snapshot.events,snapshot.artifacts)
    if not vault.is_demo or 'cardio-recommendation' not in current:raise ValueError('Requires the existing fictional Jane review dataset.')
    stamp=envelope.now_ts();date=datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(SYDNEY).date().isoformat()
    members={m['name']:m for m in care_team.listing(snapshot.events,snapshot.artifacts,profile.read(snapshot.events)['values'])}
    planned=letters(date,current['cardio-recommendation'].payload)
    missing=[item['author'] for item in planned if item['author'] not in members]
    if missing:raise ValueError('Authors not in Jane\'s existing care team: '+', '.join(missing))
    additions=[]
    for item in planned:
        existing=current.get(item['id'])
        if existing and existing.payload.get('specialist_edition')==EDITION:continue
        member=members.get(item['author'])
        if not member:raise ValueError('Author is not in Jane\'s existing care team: '+item['author'])
        text='\n\n'.join(heading+'\n'+body for heading,body in item['sections'])
        result=ingest.ingest_bytes(vault,letter_pdf(item,date,stamp,member['organisation'],member.get('location')),ingest.CaptureContext(source='import',original_filename=item['title']+'.pdf',note='New fictional specialist letter; previous originals retained.'))
        native=dict(title=item['title'],date=date,authored_date=date,received_date=date,organisation=member['organisation'],specialty=item['role'],professional=item['author'],recipient='Dr James Smith, Sydney Aged Care',patient={'name':'Jane Doe','dob':'1 November 1935'},sections=[{'heading':heading,'text':body} for heading,body in item['sections']],medications=[],prescriptions=[],results=[],release=None)
        p=copy.deepcopy(existing.payload) if existing else {}
        previous=p.get('artifact')
        p.update(demo_item_id=item['id'],review_dataset='jane-review-v2',demo_scenario=SCENARIO,fictional=True,patient_visible=True,access='clinical',
            event_type='letter',source_type='Specialist correspondence',status='New fictional source; awaiting clinician review',author=item['author'],role=item['role'],title=item['title'],text=text,
            artifact=result.short,event_date=date,date=date,document_date=date,received=date,native_document=native,specialist_edition=EDITION,document_revision=True,edition_prepared_at=stamp)
        if previous:
            p['previous_artifacts']=list(dict.fromkeys([*p.get('previous_artifacts',[]),previous]))
            p['previous_source_dates']={'event_date':existing.payload['event_date'],'document_date':existing.payload['native_document']['date'],'received_date':existing.payload['native_document']['received_date']}
        vault.append(envelope.new('note.recorded',vault.identity.id,ts=stamp,payload={'clinical_upload':result.short,'author':item['author']}))
        vault.append(envelope.new('note.recorded',vault.identity.id,ts=stamp,payload=p))
        additions.append(dict(id=item['id'],artifact=result.short,previous_artifact=previous,event_date=date,document_date=date,received_date=date,prepared_at=stamp,fictional=True,purpose=item['title']))
    if additions:
        manifest=vault.root/'DATASET-MANIFEST.json';data=json.loads(manifest.read_text()) if manifest.exists() else {'fictional':True}
        data.setdefault('specialist_letter_editions',[]).extend(additions)
        for item in additions:
            row=next((r for r in data.setdefault('sources',[]) if r['id']==item['id']),None)
            if row:
                row.setdefault('retained_original_artifacts',[]).append(row['artifact']);row.update({k:v for k,v in item.items() if k!='previous_artifact'})
            else:data['sources'].append(item)
        manifest.write_text(json.dumps(data,indent=2)+'\n')
    return additions

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--vault',required=True)
    print(json.dumps(upgrade(parser.parse_args().vault),indent=2))
