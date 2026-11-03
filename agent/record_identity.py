"""Persisted administrative facility correction; originals stay in the recovery log.

An identity update changes active presentations and source aliases, not clinical
facts, source IDs, review decisions or consultation dates.
"""
import re
from dataclasses import replace

FIELD='facility_identity_update'
_OLD=re.compile(r'Example\s+Gardens(?:\s+Residential\s+Aged\s+Care)?(?: \(fictional\))?',re.I)

def rename(value):
    if isinstance(value,str):return re.sub(r'Sydney Aged Care\s*\(fictional\)','Sydney Aged Care',_OLD.sub('Sydney Aged Care',value),flags=re.I).replace('examplegardens.example.invalid','sydneyagedcare.example.invalid').replace('Sydney Aged Care · GP room, Sydney Aged Care','Sydney Aged Care · GP room')
    if isinstance(value,list):return [rename(v) for v in value]
    if isinstance(value,tuple):return tuple(rename(v) for v in value)
    if isinstance(value,dict):return {rename(k):rename(v) for k,v in value.items()}
    return value

def settings(events):
    updates=[e.payload[FIELD] for e in events if isinstance(e.payload.get(FIELD),dict)]
    aliases={}
    for update in updates:aliases.update(update.get('artifact_map',{}))
    return bool(updates),aliases

def resolve(token,events):
    _,aliases=settings(events);seen=set()
    while token in aliases and token not in seen:seen.add(token);token=aliases[token]
    return token

def view_events(events):
    events=tuple(events);active,aliases=settings(events)
    if not active:return events
    def mapped(value):
        value=rename(value)
        if isinstance(value,str):
            if value in aliases:return resolve(value,events)
            for old,new in aliases.items():value=value.replace('/artifact/'+old,'/artifact/'+new)
            return value
        if isinstance(value,list):return [mapped(v) for v in value]
        if isinstance(value,dict):return {k:mapped(v) for k,v in value.items()}
        return value
    page_counts={}
    for e in events:
        if isinstance(e.payload.get(FIELD),dict):page_counts.update(e.payload[FIELD].get('artifact_pages',{}))
    result=[]
    for event in events:
        if event.type=='artifact.ingested' and event.payload.get('short') in aliases:continue
        if event.type.startswith(('clinician.note.','compartment.')) or FIELD in event.payload:result.append(event);continue
        payload=mapped(event.payload)
        if payload.get('artifact') in page_counts and isinstance(payload.get('native_document'),dict):
            payload['native_document']['document_pages']=page_counts[payload['artifact']]
            payload['native_document']['original_document_layout']=True
        result.append(replace(event,payload=payload,provenance=mapped(event.provenance) if event.provenance else event.provenance))
    return tuple(result)
