"""Standard, explicitly fictional pathology panels and immutable PDF editions."""
import copy
import json
from pathlib import Path
from datetime import datetime, timezone
from .. import ingest, files
from ..events import envelope
from ..demo_metadata import entries, SCENARIO
from ..server.state import RecordState
from ..vault import Vault

FORMAT='standard-pathology-v1'

def r(test,result,unit,reference,flag='',previous=''):
    return [test,result,unit,reference,flag,previous]

ELECTROLYTES=[
 r('Sodium','122','mmol/L','135–145','LOW / CRITICAL','138'),
 r('Potassium','4.3','mmol/L','3.5–5.2','','4.2'),
 r('Chloride','90','mmol/L','95–110','LOW','102'),
 r('Bicarbonate','24','mmol/L','22–32','','25'),
 r('Calcium','2.28','mmol/L','2.10–2.60','','2.32'),
 r('Magnesium','0.82','mmol/L','0.70–1.10','','0.82'),
 r('Phosphate','1.08','mmol/L','0.80–1.50','','1.10'),
]
FBE=[
 r('Haemoglobin','101','g/L','115–165','LOW','128'),
 r('Red cell count','3.45','x10^12/L','3.80–5.20','LOW','4.30'),
 r('Haematocrit','0.310','L/L','0.350–0.460','LOW','0.390'),
 r('MCV','90','fL','80–100','','91'), r('MCH','29.3','pg','27.0–33.0','','29.8'),
 r('MCHC','326','g/L','320–360','','328'),r('RDW','14.2','%','11.5–14.5','','13.5'),
 r('White cell count','6.8','x10^9/L','4.0–11.0','','6.5'),
 r('Neutrophils','4.2','x10^9/L','2.0–7.5','','4.0'),
 r('Lymphocytes','1.8','x10^9/L','1.0–4.0','','1.7'),
 r('Monocytes','0.5','x10^9/L','0.2–1.0','','0.5'),
 r('Eosinophils','0.2','x10^9/L','0.0–0.5','','0.2'),
 r('Basophils','0.1','x10^9/L','0.0–0.2','','0.1'),
 r('Platelet count','245','x10^9/L','150–400','','250'),
]
RENAL=[r('Urea','6.1','mmol/L','2.5–8.0','','5.8'),r('Creatinine','74','µmol/L','45–90','','72')]
TITLES={'critical-sodium':'Electrolyte panel','low-haemoglobin':'Full blood examination',
        'stable-renal':'Renal function panel','previous-pathology':'Full blood examination and biochemistry'}

def enrich(item):
    """Add newly authored fictional analytes; preserve every previously stated value."""
    id=item.get('id') or item.get('demo_item_id')
    if id not in TITLES:return copy.deepcopy(item)
    result=copy.deepcopy(item)
    if id=='critical-sodium':rows=copy.deepcopy(ELECTROLYTES)
    elif id=='low-haemoglobin':rows=copy.deepcopy(FBE)
    elif id=='stable-renal':rows=[copy.deepcopy(ELECTROLYTES[1]),*copy.deepcopy(RENAL)]
    else:
        rows=[r(row[0],row[5],row[2],row[3]) for row in FBE+ELECTROLYTES+RENAL if row[0]!='Potassium']
        # Potassium is included once; historical original result 4.2 is retained.
        rows.insert(len(FBE)+1,r('Potassium','4.2','mmol/L','3.5–5.2'))
    result.update(title=TITLES[id],ordered_panel=TITLES[id],results=rows,pathology_format=FORMAT)
    lines=['Test | Result | Units | Reference interval | Flag'+(' | Previous (14 Apr 2026)' if id!='previous-pathology' else '')]
    lines += [' | '.join(row[:5]+([row[5] or '—'] if id!='previous-pathology' else [])) for row in rows]
    if id=='critical-sodium':lines+=['Laboratory critical alert: sodium below this fictional laboratory’s critical threshold of 125 mmol/L.']
    result['text']='\n'.join(lines)
    return result


def report_pdf(item,prepared):
    """Facility-specific laboratory layout; preserve supplied analytes and dates."""
    from .document_templates import pathology
    from ..document_titles import TEST_NAMES
    key=item.get('id') or item['demo_item_id'];native=item.get('native_document') or {}
    doc=dict(title=TEST_NAMES.get(item['title'],item['title']),event_date=item.get('date') or item.get('event_date'),document_date=item.get('document_date') or native.get('date'),
        received_date=item.get('received') or native.get('received_date'),specimen='EDTA whole blood' if key=='low-haemoglobin' else 'EDTA whole blood / serum' if key=='previous-pathology' else 'Serum',
        accession='DEMO-'+key.upper()+'-'+(item.get('date') or item.get('event_date','')).replace('-',''),results=item['results'],laboratory_flag=item.get('laboratory_flag'))
    return pathology(doc)[0]


def native_results(rows):
    return [dict(test=row[0],result=row[1],unit=row[2],reference=row[3],reference_interval=row[3],flag=row[4],previous_result=row[5] if len(row)>5 else '',previous_date='2026-04-14' if len(row)>5 and row[5] else None) for row in rows]


def upgrade(root):
    """Append new editions only; retain source IDs, dates, originals and audit events."""
    vault=root if isinstance(root,Vault) else Vault.open(Path(root));snapshot=RecordState(vault).snapshot()
    if not vault.is_demo or not any(e.payload.get('review_dataset')=='jane-review-v2' for e in snapshot.events):raise ValueError('Only the refreshed fictional Jane demonstration can be upgraded.')
    current=entries(snapshot.events,snapshot.artifacts);added=[]
    for id in TITLES:
        event=current.get(id)
        if not event or event.payload.get('pathology_format')==FORMAT:continue
        p=copy.deepcopy(event.payload);item=enrich(dict(p,id=id,date=p['event_date'],document_date=p['native_document']['date'],received=p['native_document']['received_date']))
        stamp=envelope.now_ts();result=ingest.ingest_bytes(vault,report_pdf(item,stamp),ingest.CaptureContext(source='import',original_filename=item['title']+'.pdf',note='Newly authored fictional pathology panel edition; earlier original retained.'))
        vault.append(envelope.new('note.recorded',vault.identity.id,ts=stamp,payload={'clinical_upload':result.short,'author':p['author']}))
        previous=p['artifact'];p.update(title=item['title'],ordered_panel=item['ordered_panel'],text=item['text'],results=item['results'],pathology_format=FORMAT,pathology_revision=True,pathology_prepared_at=stamp,
            artifact=result.short,previous_artifacts=list(dict.fromkeys([*p.get('previous_artifacts',[]),previous])))
        p['native_document'].update(title=item['title'],ordered_panel=item['ordered_panel'],results=native_results(item['results']),sections=[],pathology_report=True)
        if id=='critical-sodium':p['native_document']['sections']=[{'heading':'Laboratory alert','text':p['laboratory_flag']}]
        vault.append(envelope.new('note.recorded',vault.identity.id,ts=stamp,payload=p));added.append(dict(id=id,previous_artifact=previous,artifact=result.short,edition_prepared_at=stamp,additional_results='Newly authored fictional panel values; original recorded values preserved.'))
    if added:
        manifest=vault.root/'DATASET-MANIFEST.json';data=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {'fictional':True}
        data.setdefault('pathology_editions',[]).extend(added)
        for row in data.get('sources',[]):
            edition=next((edition for edition in added if edition['id']==row['id']),None)
            if edition:
                row.setdefault('retained_original_artifacts',[]).append(row['artifact'])
                row.update(artifact=edition['artifact'],ordered_panel=TITLES[row['id']],uploaded_at=edition['edition_prepared_at'],purpose=TITLES[row['id']])
        files.write_text(manifest,json.dumps(data,indent=2)+'\n')
    return added

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--vault',required=True)
    print(json.dumps(upgrade(parser.parse_args().vault),indent=2))
