"""Append comprehensive fictional specialist correspondence; never overwrite originals."""
import copy
import json
import textwrap
from pathlib import Path
from datetime import datetime
from .. import ingest, care_team, profile, files
from ..events import envelope
from ..vault import Vault
from ..demo_metadata import entries, SCENARIO
from ..server.state import RecordState
from ..server.comparison import SYDNEY

EDITION='specialist-correspondence-v2'


def letters(date,old):
    from .correspondence_fixtures import encounters,text_of
    result=[]
    for key,item in encounters(date).items():
        if key=='physio':continue
        d=dict(item,id=key,title=item['author']+' - '+item['role']+' letter')
        d['sections']=[('Clinical correspondence',text_of(item))]
        result.append(d)
    return result


def letter_pdf(item,date,prepared,organisation,location):
    from .document_templates import letter
    return letter(dict(item,organisation=organisation.replace(' (fictional)',''),document_date=date))[0]


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
        native=dict(title=item['title'],date=date,authored_date=date,received_date=date,organisation=member['organisation'],specialty=item['role'],professional=item['author'],recipient='Dr James Smith, Sydney Aged Care',patient={'name':'Jane Doe','dob':'1 November 1935'},sections=[{'heading':heading,'text':body} for heading,body in item['sections']],medications=[],prescriptions=[],results=[],release=None,facility_template='facility-documents-v1',original_document_layout=True)
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
        manifest=vault.root/'DATASET-MANIFEST.json';data=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {'fictional':True}
        data.setdefault('specialist_letter_editions',[]).extend(additions)
        for item in additions:
            row=next((r for r in data.setdefault('sources',[]) if r['id']==item['id']),None)
            if row:
                row.setdefault('retained_original_artifacts',[]).append(row['artifact']);row.update({k:v for k,v in item.items() if k!='previous_artifact'})
            else:data['sources'].append(item)
        files.write_text(manifest,json.dumps(data,indent=2)+'\n')
    return additions

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--vault',required=True)
    print(json.dumps(upgrade(parser.parse_args().vault),indent=2))
