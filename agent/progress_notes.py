"""Sign-in-authorised progress notes; encrypted at rest with a machine-held key.

The key stays outside the synced vault. Original passphrase-encrypted events are
never overwritten. This is server-managed encryption, not end-to-end encryption.
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from datetime import date
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from . import device,sealed
from .clinician_auth import vault_id
from .events import envelope

TYPE='clinician.note.sealed'
AAD='care-record-progress-notes-v1'


def key_path(root):return device.config_home()/'clinician-note-keys'/(vault_id(root)+'.json')


def machine_key(state,create=False):
    path=key_path(state.vault.root)
    if not path.exists():
        if not create:return None
        if any(e.type==TYPE for e in state.snapshot().events):
            raise ValueError('The progress-note encryption key is missing. Restore this machine’s note-key backup; existing notes have not been changed.')
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        body={'version':1,'id':secrets.token_hex(16),'key':base64.b64encode(os.urandom(32)).decode()}
        try:
            descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(descriptor,'w') as file:json.dump(body,file)
        except FileExistsError:pass
    try:
        data=json.loads(path.read_text());key=base64.b64decode(data['key'],validate=True)
    except (ValueError,KeyError,TypeError):raise ValueError('The progress-note encryption key could not be read. Restore the matching note-key backup.') from None
    if len(key)!=32:raise ValueError('The progress-note encryption key is invalid. Existing notes have not been changed.')
    return data['id'],key


def store(state,content,request_id=None,source_event=None):
    key_id,key=machine_key(state,create=True);iv=os.urandom(12)
    cipher=AESGCM(key).encrypt(iv,json.dumps(content,ensure_ascii=False).encode(),AAD.encode())
    payload={'version':1,'key_id':key_id,'cipher':'AES-256-GCM','aad':AAD,
             'iv':base64.b64encode(iv).decode(),'ciphertext':base64.b64encode(cipher).decode(),
             'how_to_open':'Base64 AES-256-GCM with the 32-byte key identified by key_id in this machine’s clinician-note-keys directory, outside the vault. Use the recorded iv and aad; the final 16 ciphertext bytes are the tag. Plaintext is UTF-8 JSON. Access is authorised by the clinician session.'}
    if request_id:payload['request_id']=request_id
    if source_event:payload['source_event']=source_event
    event=envelope.new(TYPE,state.vault.identity.id,ts=envelope.format_ts(state.now()),payload=payload)
    state.append(event);return event


def listing(state):
    events=state.snapshot().events;managed=[e for e in events if e.type==TYPE];migrated={e.payload.get('source_event') for e in managed}
    _,legacy=sealed.read(events)
    rows=[];key_data=machine_key(state) if managed else None
    if managed and key_data is None:raise ValueError('The progress-note encryption key is missing. Restore this machine’s note-key backup; existing notes have not been changed.')
    for event in managed:
        p=event.payload
        if p['key_id']!=key_data[0]:raise ValueError('A progress note needs a different machine encryption key. Restore the matching note-key backup.')
        try:
            content=json.loads(AESGCM(key_data[1]).decrypt(base64.b64decode(p['iv']),base64.b64decode(p['ciphertext']),AAD.encode()))
        except Exception:raise ValueError('A progress note could not be decrypted. The original encrypted record has been retained.') from None
        rows.append(dict(content,id=event.id,saved_at=event.ts,imported=bool(p.get('source_event'))))
    rows.sort(key=lambda row:(row.get('written',''),row['saved_at'],row['id']),reverse=True)
    return {'notes':rows,'legacy_locked_count':sum(n.event_id not in migrated for n in legacy),'protection':'Clinician sign-in required. Notes are encrypted at rest with a key held on this computer.'}


def add(state,account,body):
    if set(body)!={'text','written','request_id'}:raise ValueError('Send the note text, date and submission ID.')
    text=body['text'];written=body['written'];token=body['request_id']
    if not isinstance(text,str) or not text.strip() or len(text)>16000:raise ValueError('Enter a note of at most 16,000 characters.')
    from .server.comparison import SYDNEY
    try:
        day=date.fromisoformat(written)
        if day.isoformat()!=written or day>state.now().astimezone(SYDNEY).date():raise ValueError()
    except (ValueError,TypeError):raise ValueError('Choose a valid note date on or before today in Australia/Sydney.') from None
    if not isinstance(token,str) or not token or len(token)>100:raise ValueError('Refresh the note form before saving.')
    with state.lock:
        prior=next((e for e in state.snapshot().events if e.type==TYPE and e.payload.get('request_id')==account['username']+':'+token),None)
        if not prior:
            store(state,{'author':account['name'],'role':account['role'],'username':account['username'],'written':written,'text':text.strip()},request_id=account['username']+':'+token)
        return listing(state)


def import_legacy(state,passphrase,fixture_only=False):
    if not isinstance(passphrase,str) or len(passphrase)>1024 or not passphrase:raise ValueError('Enter the original encryption passphrase for the older notes.')
    from .demo.progress import COMPARTMENT,NOTES
    with state.lock:
        events=state.snapshot().events;compartments,notes=sealed.read(events)
        imported={e.payload.get('source_event') for e in events if e.type==TYPE}
        allowed={n['ciphertext'] for n in NOTES}
        pending=[]
        for compartment in compartments:
            if fixture_only and (compartment.id!=COMPARTMENT['compartment'] or compartment.kdf!=COMPARTMENT['kdf'] or compartment.check!=COMPARTMENT['check']):continue
            try:
                kdf=compartment.kdf
                if kdf.get('name')!='PBKDF2' or kdf.get('hash')!='SHA-256' or not isinstance(kdf.get('iterations'),int) or not sealed.MIN_ITERATIONS<=kdf['iterations']<=sealed.MAX_ITERATIONS:continue
                key=hashlib.pbkdf2_hmac('sha256',passphrase.encode(),base64.b64decode(kdf['salt']),kdf['iterations'],32)
                checked=AESGCM(key).decrypt(base64.b64decode(compartment.check['iv']),base64.b64decode(compartment.check['ciphertext']),compartment.id.encode()).decode()
                if checked!=sealed.CHECK_PLAINTEXT:continue
            except Exception:continue
            for note in notes:
                if note.compartment!=compartment.id or note.event_id in imported or (fixture_only and note.ciphertext not in allowed):continue
                try:
                    content=json.loads(AESGCM(key).decrypt(base64.b64decode(note.iv),base64.b64decode(note.ciphertext),compartment.id.encode()))
                    if not isinstance(content.get('text'),str):continue
                    pending.append((note,content))
                except Exception:continue
        if not pending:
            if any(n.event_id not in imported for n in notes):raise ValueError('That passphrase did not open any remaining older notes. Their originals are unchanged.')
            return {'imported':0,**listing(state)}
        for note,content in pending:
            store(state,{'author':content.get('author') or 'Author not recorded','role':content.get('role') or 'Original note author',
                         'written':content.get('written') or '', 'text':content['text'],'original_saved_at':note.ts},source_event=note.event_id)
        return {'imported':len(pending),**listing(state)}


if __name__=='__main__':
    from .vault import Vault
    from .server.state import RecordState
    parser=argparse.ArgumentParser();parser.add_argument('--vault',required=True);parser.add_argument('--migrate-demo',action='store_true');args=parser.parse_args()
    if not args.migrate_demo:parser.error('Use --migrate-demo only for the known fictional note fixtures.')
    state=RecordState(Vault.open(args.vault))
    if not state.vault.is_demo:raise SystemExit('Demo migration requires a fictional demo vault.')
    result=import_legacy(state,os.environ.get('CARE_LEGACY_NOTES_PASSWORD',''),fixture_only=True)
    print(json.dumps({'imported':result['imported'],'remaining_legacy_notes':result['legacy_locked_count']}))
