"""Clinician structured record backed by existing claim corrections, not a second chart."""
import hashlib
import json
import re
from datetime import date
from fastapi import HTTPException
from .. import profile, prescriptions
from ..events import envelope
from ..projection import subjects
from .comparison import SYDNEY, day
from .review_workflow import sources, dashboard

FUNCTIONS={'mobility':('Mobility',r'walk|frame|mobility|walking'), 'personal-care':('Personal care',r'shower|bathing|dress|personal care'),
 'continence':('Continence',r'continen|toilet'), 'cognition':('Cognition',r'cognit|memory|orientat'),
 'communication':('Communication',r'hearing|communicat|plain.language|time to answer'), 'care-preferences':('Care preferences',r'prefers|preference|enjoys|gardening|values')}
FIELDS={'history':('name','status','first_documented','description','record_type'),
 'medications':('name','dose','route','frequency','status','first_documented','description'),
 'allergies':('name','reaction','verification','first_documented','description'),
 'function':('name','description','function_basis','first_documented')}


def value(entity,key,default=''):
    slot=entity.slots.get(key) if entity else None
    return (default if slot.winner.value.literal=='Not recorded' else slot.winner.value.literal) if slot and slot.winner else default


def reference(p):
    native=p.get('native_document') or {};artifact=p.get('artifact')
    return dict(id=p['demo_item_id'],artifact=artifact,title=native.get('ordered_panel') or p['title'],author=p['author'],role=p['role'],
        date=native.get('date') or p.get('event_date'),received_date=native.get('received_date'),
        url='/api/clinician/artifact/'+artifact if artifact else '/clinician/observation/'+p['demo_item_id'])


def revision(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,default=str).encode()).hexdigest()[:24]


def build(state):
    snapshot=state.snapshot();events=snapshot.events;docs=sources(snapshot);entities=snapshot.projection.entities
    source_list=[reference(p) for p in docs.values()]
    by_artifact={p.get('artifact'):p for p in docs.values() if p.get('artifact')}
    audit=[dict(e.payload['clinical_record_edit'],id=e.id,at=e.ts) for e in events if isinstance(e.payload.get('clinical_record_edit'),dict)]
    result={kind:[] for kind in FIELDS};values=profile.read(events)['values']
    scripts=prescriptions.listing(events,snapshot.artifacts,state.now(),snapshot.projection)
    def enrich(row,entity=None,related_docs=None):
        history=[a for a in audit if a['entry']==row['id']]
        ids=set(entity.sources if entity else ())
        attached=[p for p in docs.values() if p.get('artifact') in ids]
        if related_docs is not None:attached=related_docs
        # Include explicit literal mentions as supporting correspondence, without
        # asserting a diagnosis/indication from a medicine's name.
        if row['kind']!='function':
            term=row['name'].strip().casefold()
            attached+= [p for p in docs.values() if term and re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',(p['title']+' '+p['text']).casefold())]
        if history:
            attached+=[docs[id] for id in history[-1]['source_ids'] if id in docs]
        row['sources']=list({p['demo_item_id']:reference(p) for p in attached}.values())
        row['sources'].sort(key=lambda p:(p['date'] or '',p['id']))
        if history:row['first_documented']=history[-1]['values'].get('first_documented') or None
        if not history and not row.get('first_documented'):
            row['first_documented']=min((p['date'] for p in row['sources'] if p['date']),default=None)
        row['author']=history[-1]['actor']['name'] if history else row['sources'][0]['author'] if row['sources'] else 'Not recorded'
        row['role']=history[-1]['actor']['role'] if history else row['sources'][0]['role'] if row['sources'] else 'Not recorded'
        row['history']=history;row['related_medicines']=[]
        row['confirmation']='Clinician-entered correction' if history else 'Extraction confirmed; clinical assessment is separate' if entity else 'Reported information; not clinically verified'
        row['revision']=revision(row)
        return row
    for id,entity in entities.items():
        if entity.is_stub or entity.subject.kind not in ('problem','med','allergy'):continue
        if not any(s.winner for s in entity.slots.values()):continue
        kind={'problem':'history','med':'medications','allergy':'allergies'}[entity.subject.kind]
        row=dict(id=id,kind=kind,name=entity.name,first_documented=value(entity,'first_documented') or None,description=value(entity,'description'))
        if kind=='history':row.update(status='past' if entity.status=='stopped' else 'active',record_type=value(entity,'record_type','condition'))
        if kind=='allergies':row.update(reaction=value(entity,'reaction','Not recorded'),verification=value(entity,'verification','reported'))
        if kind=='medications':
            slot=entity.slots.get('dose');fields=slot.winner.value.fields if slot and slot.winner else {}
            directions=value(entity,'dose')
            frequency=value(entity,'frequency') or {'1/day':'Once daily','2/day':'Twice daily','3/day':'Three times daily'}.get(fields.get('frequency'),fields.get('instruction',''))
            row.update(dose=' '.join(str(fields.get(k,'')) for k in ('amount','unit')).strip() or directions,route=value(entity,'route'),frequency=frequency,
                directions=directions,status='stopped' if entity.status=='stopped' else 'active',prescriptions=[p for p in scripts if p.get('medicine_id')==id],supply='Supply remaining not recorded',stale=entity.stale)
            row['changes']=[dict(value=c.value.literal,confirmation='Clinician correction' if c.is_correction else 'Confirmed extraction' if c.event_id in slot.endorsed else 'Unconfirmed extraction',date=c.occurred_at.value.isoformat() if c.occurred_at else None,artifact=c.artifact,
                url='/api/clinician/artifact/'+c.artifact if c.artifact in snapshot.artifacts else None) for c in (slot.all_claims if slot else ())]
        result[kind].append(enrich(row,entity))
    # Profile reaction is reported evidence, not an absent/negative allergy history.
    report=values.get('allergy_notes','').strip()
    if report and report.casefold() not in ('not provided','unknown'):
        parts=re.split(r'\s*(?::|—|–)\s*',report,maxsplit=1)
        name=parts[0].strip() if len(parts)>1 else 'Reported reaction'
        if not any(r['name'].casefold()==name.casefold() for r in result['allergies']):
            row=dict(id='allergy:'+str(subjects.slugify(name) or 'profile-report'),kind='allergies',name=name,reaction=report,verification='reported',first_documented=None,description='Original patient-profile report; original timing and severity may be uncertain.')
            result['allergies'].append(enrich(row))
    person='person:'+str(subjects.slugify(values.get('full_name')) or 'patient')
    entity=entities.get(person)
    for key,(name,pattern) in FUNCTIONS.items():
        field='function.'+key;stored=value(entity,field)
        contributions=[]
        for p in docs.values():
            if p.get('upload_only'):continue
            if not (p.get('access')=='submission' or re.search(r'baseline|nursing|physiotherap|usual function|care preferences',p['title'],re.I)):continue
            fragments=[t.strip() for t in re.split(r'(?<=[.!?])\s+',p['text']) if re.search(pattern,t,re.I)]
            if fragments:
                contributions.append(dict(text=' '.join(fragments),date=p.get('event_date'),basis='Usual function' if re.search('baseline|usual',p['title'],re.I) else 'Documented assessment' if 'assessment' in p['title'].lower() else 'Dated observation; may reflect a temporary change',source=reference(p)))
        accessibility=values.get('accessibility','').split('\nClinician-recorded functional updates:',1)[0]
        fragments=[t.strip() for t in re.split(r'(?<=[.!?])\s+',accessibility) if re.search(pattern,t,re.I)]
        if fragments:contributions.append(dict(text=' '.join(fragments),date=None,basis='Recorded profile information; source date not supplied',source=None))
        related=[docs[c['source']['id']] for c in contributions if c['source']]
        row=dict(id=person+'#'+field,kind='function',name=name,description=stored or (contributions[0]['text'] if contributions else ''),
            first_documented=value(entity,field+'.date') or None,function_basis=value(entity,field+'.basis','Source-specific observations'),contributions=contributions)
        result['function'].append(enrich(row,related_docs=related))
    for row in result['history']:
        # A prescription record explicitly *for* a named condition supplies the
        # relationship; merely appearing in the same document does not.
        for p in docs.values():
            if not re.search(r'prescription record for\s+'+re.escape(row['name'])+r'\b',p['title'],re.I):continue
            issued=re.search(r'\b(?:issued|prescribed)\s+(.+?)(?:following|\.\s|$)',p['text'],re.I)
            if issued:
                for med in result['medications']:
                    if re.search(r'\b'+re.escape(med['name'])+r'\b',issued.group(1),re.I):row['related_medicines'].append(dict(id=med['id'],name=med['name'],directions=med['directions'],source=reference(p)))
    for rows in result.values():
        for row in rows:row['revision']=revision({k:v for k,v in row.items() if k!='revision'})
        if rows is not result['function']:rows.sort(key=lambda r:r['name'].casefold())
    return dict(sections=result,source_options=[{k:v for k,v in p.items() if k!='text'} for p in source_list],today=state.now().astimezone(SYDNEY).date().isoformat(),
        guidance='First documented dates are source dates, not symptom or condition onset. Structured corrections do not issue prescriptions or release documents.')


def mutate(state,account,body):
    if not isinstance(body,dict) or set(body)-{'id','kind','values','revision','source_ids','basis','request_id'}:raise HTTPException(400,'Check the entry form.')
    kind=body.get('kind');written=body.get('values');key=body.get('id');token=body.get('request_id')
    if kind not in FIELDS or not isinstance(written,dict) or set(written)!=set(FIELDS[kind]):raise HTTPException(400,'Check the structured entry fields.')
    if any(not isinstance(v,str) or len(v)>4000 for v in written.values()):raise HTTPException(400,'Use text values of 4,000 characters or fewer.')
    written={k:v.strip() for k,v in written.items()}
    if not written['name']:raise HTTPException(400,'Enter a name.')
    if not isinstance(token,str) or not token or len(token)>100:raise HTTPException(400,'A submission identifier is required.')
    basis=body.get('basis')
    if not isinstance(basis,str) or not basis.strip() or len(basis)>1500:raise HTTPException(400,'Record the reason or evidence for this entry.')
    if written['first_documented']:
        try:
            d=date.fromisoformat(written['first_documented'])
            if d.isoformat()!=written['first_documented'] or d>state.now().astimezone(SYDNEY).date():raise ValueError()
        except ValueError:raise HTTPException(400,'Use a valid first-documented date, not a future date.')
    if kind in ('history','medications') and written['status'] not in ('active','past' if kind=='history' else 'stopped'):raise HTTPException(400,'Choose a valid recorded status.')
    if kind=='medications' and not written['dose']:raise HTTPException(400,'Record the dose/directions from the source.')
    if kind=='allergies' and written['verification'] not in ('reported','verified','refuted'):raise HTTPException(400,'Choose a verification status.')
    if kind=='history' and written['record_type'] not in ('condition','procedure'):raise HTTPException(400,'Choose condition or procedure.')
    if kind=='function' and written['function_basis'] not in ('Usual function','Temporary change','Current assessment','Source-specific observations'):raise HTTPException(400,'Choose the functional assessment basis.')
    with state.lock:
        dashboard(state,account)  # Existing assignment check.
        snapshot=state.snapshot();docs=sources(snapshot)
        selected=body.get('source_ids')
        if not isinstance(selected,list) or any(not isinstance(id,str) or id not in docs for id in selected):raise HTTPException(400,'Select existing source documents.')
        actor={k:account[k] for k in ('username','name','role')}
        duplicate=next((e.payload['clinical_record_edit'] for e in snapshot.events if e.payload.get('clinical_record_edit',{}).get('request_id')==token and e.payload.get('clinical_record_edit',{}).get('actor',{}).get('username')==actor['username']),None)
        if duplicate:return dict(id=duplicate['entry'],duplicate=True)
        data=build(state);previous=next((r for r in data['sections'][kind] if r['id']==key),None)
        if key and not previous:raise HTTPException(404,'The entry was not found.')
        if previous and body.get('revision')!=previous['revision']:raise HTTPException(409,'This entry changed. Reload before editing it.')
        if not key:
            if kind=='function':raise HTTPException(400,'Select a functional domain to edit.')
            slug=subjects.slugify(written['name'])
            if not slug:raise HTTPException(400,'Use a valid entry name.')
            key={'history':'problem:','medications':'med:','allergies':'allergy:'}[kind]+slug
            if any(r['id']==key for r in data['sections'][kind]):raise HTTPException(409,'This entry exists; edit it instead.')
        subject,_,function=key.partition('#')
        if not subjects.parse(subject):raise HTTPException(400,'Invalid entry identifier.')
        fields=written.copy()
        if kind=='function':
            fields={function:written['description'],function+'.date':written['first_documented'],function+'.basis':written['function_basis']}
            if not written['description']:raise HTTPException(400,'Record the functional observation.')
        elif kind=='medications':fields['dose']=' '.join(v for v in (written['dose'],written['frequency']) if v)
        elif kind=='history':fields['status']='stopped' if written['status']=='past' else 'active'
        stamp=envelope.format_ts(state.now());device=state.vault.identity.id
        audit=dict(entry=key,kind=kind,actor=actor,previous={k:previous.get(k,'') for k in FIELDS[kind]} if previous else None,
            values=written,source_ids=list(dict.fromkeys(selected)),basis=basis.strip(),request_id=token)
        # Keep the audit and corrections private to the clinician projection.
        # Original sources and their release state are never changed by an edit.
        for predicate,field_value in fields.items():
            payload=dict(subject=subject,predicate=predicate,value=field_value or "Not recorded",subject_name=written['name'] if kind!='function' else profile.read(snapshot.events)['values']['full_name'],evidence_tier='prescriber-issued',submission_workspace='clinician',clinical_record_actor=actor)
            if selected and docs[selected[0]].get('artifact'):payload['artifact']=docs[selected[0]]['artifact']
            state.append(envelope.new('claim.corrected',device,ts=stamp,payload=payload))
        if kind=='function':
            current=state.snapshot();person=current.projection.entities.get(subject)
            updates={key.split('.',1)[1]:slot.winner.value.literal for key,slot in person.slots.items() if key.startswith('function.') and key.count('.')==1 and slot.winner and slot.winner.value.literal!='Not recorded'}
            original_accessibility=profile.read(snapshot.events)['values'].get('accessibility','')
            baseline=original_accessibility.split('\nClinician-recorded functional updates:',1)[0]
            summary=baseline+'\nClinician-recorded functional updates: '+'; '.join(k.replace('-', ' ').title()+': '+v for k,v in updates.items())
            state.append(envelope.new(profile.UPDATED,device,ts=stamp,payload={'values':{'accessibility':summary},'submission_workspace':'clinician','clinical_record_actor':actor,'previous_values':{'accessibility':original_accessibility}}))
        state.append(envelope.new('note.recorded',device,ts=stamp,payload={'submission_workspace':'clinician','clinical_record_edit':audit}))
        return dict(id=key,duplicate=False)
