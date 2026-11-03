"""Administrative rename preserves source identity, review/release and encrypted notes."""
import io,copy,unittest
from pathlib import Path
from datetime import datetime,timezone
import pdfplumber
from tests import test_review_workflow as base
from agent import profile,ingest,progress_notes
from agent.demo.jane import source_pdf
from agent.demo.calendar_data import CARE_TEAM
from agent.demo_metadata import entries
from agent.demo.rename_facility import migrate
from agent.events import envelope
from agent.server.state import RecordState

class FacilityRenameTests(unittest.TestCase):
 setUp=base.ReviewWorkflowTests.setUp
 login=base.ReviewWorkflowTests.login
 data=base.ReviewWorkflowTests.data
 post=base.ReviewWorkflowTests.post
 issue=base.ReviewWorkflowTests.issue
 def test_rename_pdfs_api_notes_and_permissions(self):
  self.app.state.record._clock=lambda:datetime.now(timezone.utc)
  self.vault.append(envelope.new(profile.UPDATED,self.vault.identity.id,payload={'values':{'address':'Example Gardens Residential Aged Care\n1 Demonstration Lane\nSydney NSW 2000','gp_practice':'Example Gardens'}}))
  team=copy.deepcopy(CARE_TEAM)
  for m in team:
   if m['name'] in ('Dr James Smith','Dr Olivia Bennett','Morgan Bell'):m['organisation']='Example Gardens Residential Aged Care'
  self.vault.append(envelope.new('note.recorded',self.vault.identity.id,payload={'demo_scenario':'jane-two-year-v1','care_team':team}))
  old=ingest.ingest_bytes(self.vault,source_pdf('Jane Doe | DOB 1 November 1935\nExample Gardens Residential Aged Care\nOriginal fictional baseline.'),ingest.CaptureContext(source='import',original_filename='baseline.pdf'))
  self.app.state.record.invalidate();source=copy.deepcopy(entries(self.app.state.record.snapshot().events,self.app.state.record.snapshot().artifacts)['baseline'].payload)
  source.update(artifact=old.short);source['native_document']['organisation']='Example Gardens Residential Aged Care'
  self.vault.append(envelope.new('note.recorded',self.vault.identity.id,payload=source));self.app.state.record.invalidate()
  progress_notes.store(self.app.state.record,{'author':'Morgan Bell','role':'Registered nurse','written':'2026-10-19','text':'Care delivered at Example Gardens.'})
  before_cipher=[e.payload['ciphertext'] for e in self.vault.read().events if e.type==progress_notes.TYPE]
  self.login();self.post('baseline','review',sources=['baseline']);self.post('baseline','release',source='baseline',recipients=['patient'],message='Baseline shared.',confirmed=True)
  before=self.issue('baseline','2024-01-01')['workflow'];self.assertTrue(before['reviewed'])
  result=migrate(self.vault);self.assertGreaterEqual(result['documents'],1);self.assertTrue(Path(result['archive']).is_dir())
  self.app.state.record.invalidate()
  for path in ('/api/profile','/api/clinician/profile','/api/clinician/dashboard','/api/clinician/timeline/feed','/api/patient/timeline/feed','/api/clinician/document-list','/api/calendar','/api/clinician/progress-notes'):
   r=self.client.get(path);self.assertEqual(r.status_code,200,r.text[:150]);self.assertNotIn('Example Gardens',r.text)
  team=self.client.get('/api/profile').json()['care_team']
  for name in ('Dr James Smith','Dr Olivia Bennett','Morgan Bell'):self.assertEqual(next(m for m in team if m['name']==name)['organisation'],'Sydney Aged Care')
  current=self.issue('baseline','2024-01-01');self.assertTrue(current['workflow']['reviewed']);self.assertEqual(len(current['workflow']['releases']),len(before['releases']))
  new=current['contributions'][0]['artifact'];self.assertNotEqual(new,old.short)
  for token in (old.short,new):
   r=self.client.get('/api/artifact/'+token);self.assertEqual(r.status_code,200)
   with pdfplumber.open(io.BytesIO(r.content)) as pdf:self.assertNotIn('Example Gardens','\n'.join(p.extract_text() or '' for p in pdf.pages))
  other=self.issue('critical-sodium')['contributions'][0]['artifact'];self.assertEqual(self.client.get('/api/artifact/'+other).status_code,404)
  self.assertEqual(before_cipher,[e.payload['ciphertext'] for e in self.vault.read().events if e.type==progress_notes.TYPE])
  self.assertEqual(self.client.get('/api/clinician/progress-notes').json()['notes'][0]['text'],'Care delivered at Sydney Aged Care.')
  self.assertTrue(migrate(self.vault)['already_applied'])
  self.app.state.record=RecordState(self.vault)
  self.assertEqual(self.client.get('/api/profile').json()['values']['address'].split('\n')[0],'Sydney Aged Care')
  self.assertEqual(self.data()['comparison_date'],'2026-10-21')
