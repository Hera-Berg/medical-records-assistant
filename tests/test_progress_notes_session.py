"""Progress notes use clinician sessions, retain ciphertext, and never enter patient data."""
import base64
import hashlib
import json
import os
import secrets
import stat
import unittest
from datetime import datetime,timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from tests import test_review_workflow as base
from agent.events import envelope
from agent import sealed,progress_notes,projection
from agent.server.state import RecordState

class ProgressNotesSessionTests(unittest.TestCase):
 setUp=base.ReviewWorkflowTests.setUp
 login=base.ReviewWorkflowTests.login
 def post_note(self,text='Private fixture consultation words',token='notes-fixture'):
  return self.client.post('/api/clinician/progress-notes',json={'text':text,'written':'2026-10-30','request_id':token},headers={'X-Clinician-CSRF':self.csrf})
 def test_authenticated_no_second_password_and_private_persistence(self):
  self.assertEqual(self.client.get('/api/clinician/progress-notes').status_code,401)
  self.assertEqual(self.client.post('/api/clinician/progress-notes',json={}).status_code,401)
  self.assertEqual(self.client.get('/clinician/progress-notes',follow_redirects=False).status_code,303)
  self.login();self.assertEqual(self.client.get('/api/clinician/progress-notes').json()['notes'],[])
  self.assertEqual(self.client.post('/api/clinician/progress-notes',json={}).status_code,403)
  r=self.post_note();self.assertEqual(r.status_code,200,r.text)
  note=r.json()['notes'][0];self.assertEqual(note['author'],'Dr James Smith');self.assertEqual(note['role'],'Usual GP')
  before=len(self.vault.read().events);self.assertEqual(self.post_note().status_code,200);self.assertEqual(len(self.vault.read().events),before)
  path=progress_notes.key_path(self.vault.root);self.assertFalse(path.is_relative_to(self.vault.root));self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
  for file in self.vault.root.rglob('*'):
   if file.is_file():self.assertNotIn(note['text'].encode(),file.read_bytes())
  for endpoint in ('/api/timeline','/api/wiki','/api/profile','/api/documents','/api/patient/documents','/api/shared-care','/api/patient/updates'):
   response=self.client.get(endpoint);self.assertEqual(response.status_code,200,response.text);self.assertNotIn(note['text'],response.text)
  preview=self.client.post('/api/clinician/summary/preview',json={},headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(preview.status_code,200,preview.text);self.assertNotIn(note['text'],preview.text)
  fresh=RecordState(self.vault);fresh._clock=lambda:datetime(2026,11,2,12,tzinfo=timezone.utc);self.app.state.record=fresh
  self.login('olivia.bennett');data=self.client.get('/api/clinician/progress-notes').json();self.assertEqual(data['notes'][0]['text'],note['text'])
  second=self.post_note('Another private fixture note','second-token');self.assertEqual(second.status_code,200);self.assertEqual(second.json()['notes'][0]['author'],'Dr Olivia Bennett')
  self.client.post('/api/auth/clinician/logout',json={},headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(self.client.get('/api/clinician/progress-notes').status_code,401)
 def test_legacy_import_preserves_original_and_runs_once(self):
  self.login();phrase=secrets.token_urlsafe(20);salt=os.urandom(16);cid='cmp-'+os.urandom(16).hex();key=hashlib.pbkdf2_hmac('sha256',phrase.encode(),salt,600000,32)
  def box(text):
   iv=os.urandom(12);return {'iv':base64.b64encode(iv).decode(),'ciphertext':base64.b64encode(AESGCM(key).encrypt(iv,text.encode(),cid.encode())).decode()}
  body={'id':cid,'kdf':{'name':'PBKDF2','hash':'SHA-256','iterations':600000,'salt':base64.b64encode(salt).decode()},'check':box(sealed.CHECK_PLAINTEXT)}
  self.app.state.record.append(envelope.new(sealed.COMPARTMENT_CREATED,self.vault.identity.id,payload=sealed.compartment_payload(body)))
  original=envelope.new(sealed.NOTE_SEALED,self.vault.identity.id,payload=sealed.note_payload({'compartment':cid,**box(json.dumps({'author':'Original fictional author','written':'2026-10-21','text':'Legacy private fixture words'}))},sealed.read(self.vault.read().events)[0]))
  self.app.state.record.append(original);before={p:p.read_bytes() for p in self.vault.events_dir.glob('*.jsonl')}
  self.assertEqual(self.client.get('/api/clinician/progress-notes').json()['legacy_locked_count'],1)
  bad=self.client.post('/api/clinician/progress-notes/import-legacy',json={'passphrase':'wrong'},headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(bad.status_code,400)
  result=self.client.post('/api/clinician/progress-notes/import-legacy',json={'passphrase':phrase},headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(result.status_code,200,result.text)
  data=result.json();self.assertEqual(data['imported'],1);self.assertEqual(data['legacy_locked_count'],0);self.assertEqual(data['notes'][0]['author'],'Original fictional author');self.assertEqual(data['notes'][0]['text'],'Legacy private fixture words')
  for path,content in before.items():self.assertTrue(path.read_bytes().startswith(content))
  again=self.client.post('/api/clinician/progress-notes/import-legacy',json={'passphrase':phrase},headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(again.json()['imported'],0)
  self.assertIn(original.to_dict(),[e.to_dict() for e in self.vault.read().events])
 def test_missing_machine_key_does_not_silently_replace_it(self):
  self.login();self.assertEqual(self.post_note().status_code,200);path=progress_notes.key_path(self.vault.root);backup=path.with_suffix('.backup');path.rename(backup)
  response=self.client.get('/api/clinician/progress-notes');self.assertEqual(response.status_code,409);self.assertIn('missing',response.json()['detail'])
  self.assertEqual(self.post_note('Second note','missing-key').status_code,400);self.assertFalse(path.exists());backup.rename(path)
  self.assertEqual(len(self.client.get('/api/clinician/progress-notes').json()['notes']),1)
