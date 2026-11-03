"""Patient timeline reuses the source feed only after backend permission filtering."""
import unittest
from tests import test_review_workflow as base
from agent.events import envelope

class PatientTimelineTests(unittest.TestCase):
 setUp=base.ReviewWorkflowTests.setUp
 login=base.ReviewWorkflowTests.login
 data=base.ReviewWorkflowTests.data
 post=base.ReviewWorkflowTests.post
 issue=base.ReviewWorkflowTests.issue
 def patient(self):
  r=self.client.get('/api/patient/timeline/feed');self.assertEqual(r.status_code,200,r.text);return r.json()
 def test_submissions_and_selected_releases_only(self):
  before=self.patient();self.assertEqual(len(before['events']),5)
  self.assertEqual({r['author'] for r in before['events']},{'Jane Doe','Emily Doe','Morgan Bell'})
  self.assertNotIn('122 mmol',str(before));self.assertNotIn('Electrolyte panel',str(before))
  self.assertTrue(all(not r['source_url'].startswith('/clinician') for r in before['events']))
  self.login();self.assertEqual(self.patient(),before)
  source=self.issue('critical-sodium')['contributions'][0]
  self.assertEqual(self.post('critical-sodium','review',sources=['critical-sodium']).status_code,200)
  self.assertEqual(self.post('critical-sodium','release',source='critical-sodium',recipients=['patient'],message='Your shared report.',confirmed=True).status_code,200)
  after=self.patient();self.assertEqual(len(after['events']),6)
  row=next(r for r in after['events'] if r['id']==source['artifact']);self.assertEqual(row['title'],'Electrolyte panel')
  self.assertNotIn('Full blood examination (FBE)',str(after))
  for row in after['events']:
   self.assertEqual(self.client.get(row['source_url']).status_code,200)
   self.assertEqual(self.client.get(row['original_url']).status_code,200)
  self.client.post('/api/auth/clinician/logout',json={},headers={'X-Clinician-CSRF':self.csrf})
  self.assertEqual(self.patient(),after)
  self.assertEqual(self.client.get('/api/clinician/timeline/feed').status_code,401)
  other=next(e for e in self.app.state.record.snapshot().events if e.payload.get('demo_item_id')=='low-haemoglobin').payload['artifact']
  self.assertEqual(self.client.get('/api/artifact/'+other).status_code,404)
 def test_own_typed_note_source_and_private_notes_do_not_leak(self):
  own=self.client.post('/api/capture/text',data={'text':'My own short note.','author':'Emily Doe','role':'Daughter'}).json()['event']
  private=envelope.new('note.recorded',self.vault.identity.id,payload={'text':'INTERNAL-CLINICAL-TEXT','source':'typed','submission_workspace':'clinician'})
  self.vault.append(private);self.vault.append(envelope.new('clinician.note.sealed',self.vault.identity.id,payload={'ciphertext':'PRIVATE-SEALED'}));self.app.state.record.invalidate()
  feed=self.patient();self.assertNotIn('INTERNAL-CLINICAL-TEXT',str(feed));self.assertNotIn('PRIVATE-SEALED',str(feed))
  row=next(r for r in feed['events'] if r['id']==own);self.assertEqual(row['source_url'],'/observation/'+own)
  original=self.client.get('/api/patient/observations/'+own);self.assertEqual(original.status_code,200);self.assertEqual(original.json()['author'],'Emily Doe');self.assertEqual(original.json()['text'],'My own short note.')
  self.assertEqual(self.client.get('/api/patient/observations/'+private.id).status_code,404)
