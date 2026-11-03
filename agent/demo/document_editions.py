"""Recoverable facility PDF editions. Only explicitly new encounters gain new clinical dates."""
import copy
import json
import shutil
from pathlib import Path
from datetime import datetime,timezone
from .. import ingest, files
from ..events import envelope
from ..vault import Vault
from ..demo_metadata import entries
from ..document_titles import title as source_title
from ..server.state import RecordState
from ..server.comparison import SYDNEY
from . import document_templates as templates
from .correspondence_fixtures import document as correspondence

FORMAT='facility-documents-v1'


def prepare(payload,day):
    p=copy.deepcopy(payload);key=p['demo_item_id'];native=p['native_document']
    if native.get('pathology_report') or native.get('results'):
        tests=p.get('results') or [[r['test'],r['result'],r['unit'],r.get('reference') or r.get('reference_interval',''),r.get('flag',''),r.get('previous_result','')] for r in native['results']]
        d=dict(title=source_title(p),organisation='Example Pathology Laboratory',author=p['author'],role=p['role'],event_date=p['event_date'],document_date=native['date'],received_date=native.get('received_date'),
            specimen='EDTA whole blood' if key=='low-haemoglobin' else 'EDTA whole blood / serum' if key=='previous-pathology' else 'Serum',
            accession='DEMO-'+{'critical-sodium':'EP','low-haemoglobin':'FBE','stable-renal':'RF','previous-pathology':'CMP'}[key]+'-'+p['event_date'].replace('-',''),
            results=tests,laboratory_flag=p.get('laboratory_flag'),new_encounter=False,additions=[])
        data,boxes=templates.pathology(d)
    else:
        d=correspondence(p,day)
        data,boxes=templates.letter(d) if d.get('blocks') else templates.clinical_note(d)
    bad=[b for b in boxes if b[1]<40 or b[1]+b[3]>555 or b[2]>825]
    if bad:raise ValueError('PDF text outside safe page bounds: '+str((key,bad)))
    return d,data,boxes


def available(vault):
    snapshot=RecordState(vault).snapshot()
    if not vault.is_demo or not any(e.payload.get('review_dataset')=='jane-review-v2' for e in snapshot.events):raise ValueError('Requires Jane\'s existing fictional review dataset.')
    return [e.payload for e in entries(snapshot.events,snapshot.artifacts).values() if e.payload.get('access')=='clinical']


def export_document(root,key,title,data):
    folder=Path(root)/key;folder.mkdir(parents=True,exist_ok=True)
    path=folder/(title.replace('/','-')+'.pdf');path.write_bytes(data);return str(path)


def preview(vault,root):
    stamp=envelope.now_ts();day=datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(SYDNEY).date().isoformat();result=[]
    for p in available(vault):
        d,data,boxes=prepare(p,day)
        path=export_document(root,p['demo_item_id'],d['title'],data)
        result.append(dict(id=p['demo_item_id'],title=d['title'],path=path,pages=max(b[0] for b in boxes),new_encounter=d['new_encounter'],new_fictional_details=d['additions']))
    return result


def upgrade(root,export=None,only=None,force=False):
    vault=root if isinstance(root,Vault) else Vault.open(Path(root));stamp=envelope.now_ts();day=datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(SYDNEY).date().isoformat()
    candidates=[p for p in available(vault) if (only is None or p['demo_item_id'] in only) and (force or p.get('facility_document_edition')!=FORMAT)]
    # Render and validate all pages before any record mutation.
    prepared=[(p,*prepare(p,day)) for p in candidates]
    if not prepared:return {'archive':None,'editions':[]}
    archive=vault.root.parent/'archives'/('jane-before-facility-documents-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    if archive.exists():raise ValueError('Archive exists; refusing to overwrite it.')
    archive.parent.mkdir(exist_ok=True);shutil.copytree(vault.root,archive)
    editions=[]
    for original,d,data,boxes in prepared:
        p=copy.deepcopy(original);previous=p['artifact'];previous_native=p['native_document']
        result=ingest.ingest_bytes(vault,data,ingest.CaptureContext(source='import',original_filename=d['title']+'.pdf',note='Fictional facility document edition. Original sources retained.'))
        vault.append(envelope.new('note.recorded',vault.identity.id,ts=stamp,payload={'clinical_upload':result.short,'author':p['author']}))
        clinical_text=d.get('text',p['text'])
        sections=([{'heading':d.get('subject') or 'Correspondence','text':clinical_text}] if d.get('blocks') else previous_native.get('sections',[]))
        # New findings belong only to explicitly new, currently dated encounters.
        # Historical restyling keeps its source event, document and receipt dates.
        native={**previous_native,'title':d['title'],'date':d['document_date'],'authored_date':d['document_date'],'received_date':d.get('received_date'),
            'organisation':d['organisation'],'specialty':d['role'],'professional':d['author'],'patient':{'name':'Jane Doe','dob':'1935-11-01','identifier':templates.PATIENT_ID},
            'facility_template':FORMAT,'original_document_layout':True,'document_pages':max(b[0] for b in boxes),'sections':sections,
            'edition_notes':{'prepared_at':stamp,'previous_artifact':previous,'new_encounter':d['new_encounter'],'new_fictional_details':d['additions'],'editorial_note':d.get('editorial_note'),
                'original_source_dates':{'event':p['event_date'],'document':previous_native.get('date'),'received':previous_native.get('received_date')}}}
        if d.get('accession'):native.update(accession=d['accession'],specimen=d['specimen'],ordered_panel=d['title'])
        if d.get('medications'):native['medications']=[{'medicine':name,'directions':directions} for name,directions in d['medications']]
        if d['new_encounter']:
            native.pop('calendar_appointment',None)
            p.pop('consultation',None)
            p.update(event_type='letter',date=day,document_date=day,received=day,fictional_encounter={'date':day,'type':d['encounter'],'added_details':d['additions']})
        p.update(title=d['title'],text=clinical_text,event_date=d['event_date'],native_document=native,artifact=result.short,
            previous_artifacts=list(dict.fromkeys([*p.get('previous_artifacts',[]),previous])),facility_document_edition=FORMAT,document_revision=result.short!=previous,pathology_revision=False,edition_prepared_at=stamp,download_filename=d['title']+'.pdf')
        if p.get('ordered_panel'):p['ordered_panel']=d['title']
        vault.append(envelope.new('note.recorded',vault.identity.id,ts=stamp,payload=p))
        path=export_document(export,p['demo_item_id'],d['title'],data) if export else None
        editions.append(dict(id=p['demo_item_id'],title=d['title'],artifact=result.short,previous_artifact=previous,prepared_at=stamp,
            event_date=d['event_date'],document_date=d['document_date'],received_date=d.get('received_date'),new_encounter=d['new_encounter'],new_fictional_details=d['additions'],
            editorial_note=d.get('editorial_note'),pages=max(b[0] for b in boxes),export_path=path,facility=d['organisation']))
    manifest=vault.root/'DATASET-MANIFEST.json';m=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {'fictional':True}
    m.setdefault('facility_document_editions',[]).append({'version':FORMAT,'archive':str(archive),'prepared_at':stamp,'facilities':{k:vars(v) for k,v in templates.FACILITIES.items()},'editions':editions,
        'administrative_details_added':'Fictional facility contacts, patient identifier and laboratory accession/specimen presentation; no real provider/registration numbers.'})
    for edition in editions:
        row=next((r for r in m.get('sources',[]) if r['id']==edition['id']),None)
        if row:
            row.setdefault('retained_original_artifacts',[]).append(row['artifact']);row.update(artifact=edition['artifact'],purpose=edition['title'],event_date=edition['event_date'],document_date=edition['document_date'],received_date=edition['received_date'],uploaded_at=stamp)
    files.write_text(manifest,json.dumps(m,indent=2)+'\n')
    if export:
        Path(export).mkdir(parents=True,exist_ok=True)
        export_manifest=Path(export)/'edition-manifest.json'
        combined=json.loads(export_manifest.read_text(encoding='utf-8')) if export_manifest.exists() else m['facility_document_editions'][-1].copy()
        existing={edition['id']:edition for edition in combined.get('editions',[])};existing.update({edition['id']:edition for edition in editions})
        combined['editions']=list(existing.values());combined.setdefault('archives',[]).append(str(archive))
        files.write_text(export_manifest,json.dumps(combined,indent=2)+'\n')
    return {'archive':str(archive),'editions':editions}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--vault',required=True);group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--preview');group.add_argument('--apply',action='store_true');parser.add_argument('--export')
    args=parser.parse_args();vault=Vault.open(Path(args.vault))
    print(json.dumps(preview(vault,args.preview) if args.preview else upgrade(vault,args.export),indent=2))
