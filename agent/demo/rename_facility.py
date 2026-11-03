"""Recoverable administrative correction of Jane's fictional residence and facility."""
import json,re,shutil
from pathlib import Path
from datetime import datetime,timezone
from ..vault import Vault
from ..server.state import RecordState
from ..events import envelope
from .. import ingest,record_identity
from ..demo_metadata import history as source_history
from ..document_titles import title
from . import document_templates as templates
from .document_editions import prepare

VERSION='sydney-aged-care-v1'

def migrate(root,export=None):
    vault=root if isinstance(root,Vault) else Vault.open(Path(root));state=RecordState(vault);snapshot=state.snapshot()
    if not vault.is_demo:raise ValueError('This correction is limited to the fictional demonstration vault.')
    if record_identity.settings(snapshot.events)[0]:return {'already_applied':True,'documents':0}
    stamp=envelope.now_ts();archive=vault.root.parent/'archives'/('jane-before-residence-rename-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    archive.parent.mkdir(exist_ok=True);shutil.copytree(vault.root,archive)
    metadata={e.payload['artifact']:e.payload for e in source_history(snapshot.events,snapshot.artifacts)}
    aliases={};page_counts={};changes=[]
    import pdfplumber
    for short,artifact in snapshot.artifacts.items():
        source=vault.root/artifact.rel
        if artifact.mime!='application/pdf' or not source.exists():continue
        with pdfplumber.open(source) as pdf:original='\n'.join(page.extract_text() or '' for page in pdf.pages)
        if record_identity.rename(original)==original:continue
        p=record_identity.rename(metadata.get(short,{}));native=p.get('native_document') or {}
        if p:
            data=None
            if p.get('facility_document_edition'):
                d,proposed,boxes=prepare(p,p['event_date'])
                # Reuse the current complex layout only when the clinical text
                # is identical after the explicit administrative name correction.
                if d.get('text',p['text'])==p['text']:data=proposed
            if data is None:
                organisation=(native.get('organisation') or 'Sydney Aged Care').replace(' (fictional)','')
                d=dict(author=p['author'],role=p['role'],organisation=organisation,title=title(p),text=p['text'],event_date=p['event_date'],document_date=native.get('date') or p['event_date'])
                if native.get('pathology_report') or native.get('results'):
                    _,data,boxes=prepare(p,p['event_date'])
                else:data,boxes=templates.clinical_note(d)
            name=title(p)+'.pdf'
        else:
            d=dict(author='Author not recorded',role='Uploaded document',organisation='Sydney Aged Care',title='Patient record',text=record_identity.rename(original),event_date=None,document_date=None)
            data,boxes=templates.clinical_note(d);name='Patient record.pdf'
        result=ingest.ingest_bytes(vault,data,ingest.CaptureContext(source='import',original_filename=name,note='Administrative facility-name correction; original retained in recovery archive.'))
        if result.short==short:continue
        aliases[short]=result.short;page_counts[result.short]=max(b[0] for b in boxes)
        changes.append({'previous_artifact':short,'artifact':result.short,'title':name[:-4],'source_id':p.get('demo_item_id')})
    # One durable alias event keeps source IDs, dates and all review/release
    # decisions attached to the exact same clinical content. Ciphertext stays intact.
    update={'version':VERSION,'from':'Example Gardens','to':'Sydney Aged Care','artifact_map':aliases,'artifact_pages':page_counts,'archive':str(archive),'prepared_at':stamp,'administrative_only':True}
    vault.append(envelope.new('note.recorded',vault.identity.id,ts=stamp,payload={record_identity.FIELD:update}))
    manifest=vault.root/'DATASET-MANIFEST.json'
    body=json.loads(manifest.read_text()) if manifest.exists() else {'fictional':True}
    body=record_identity.rename(body);body['facility_identity_update']={'version':VERSION,'facility':'Sydney Aged Care','prepared_at':stamp,'archive':str(archive),'documents':changes}
    manifest.write_text(json.dumps(body,indent=2)+'\n')
    if export:
        from .document_editions import export_document
        active=state.snapshot();current={e.payload['demo_item_id']:e.payload for e in __import__('agent.demo_metadata',fromlist=['entries']).entries(active.events,active.artifacts).values()}
        for p in current.values():
            a=active.artifacts[p['artifact']]
            if a.mime=='application/pdf':export_document(export,p['demo_item_id'],title(p),(vault.root/a.rel).read_bytes())
    return {'archive':str(archive),'documents':len(changes),'facility':'Sydney Aged Care'}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--vault',required=True);parser.add_argument('--export');args=parser.parse_args()
    print(json.dumps(migrate(args.vault,args.export),indent=2))
