"""Capture attribution, immediate clinician review and document release reuse."""
import unittest
from tests.test_patient_portal import PatientPortalTests

class UpdateCaptureTests(unittest.TestCase):
 setUp=PatientPortalTests.setUp
 login=PatientPortalTests.login
 data=PatientPortalTests.data
 post=PatientPortalTests.post
 issue=PatientPortalTests.issue
 def test_attributed_updates_and_uploads_reach_review_without_inference(self):
  from datetime import datetime,timezone
  self.app.state.record._clock=lambda:datetime.now(timezone.utc)
  response=self.client.post('/api/capture/text',data={'text':'Feels dizzy when standing.','author':'Emily Doe','role':'Daughter','observation_date':'2026-11-01'})
  self.assertEqual(response.status_code,202,response.text);key=response.json()['event']
  upload=self.client.post('/api/capture',files={'files':('update.txt',b'Original submitted report','text/plain')},data={'author':'Morgan Bell','role':'Registered nurse'})
  self.assertEqual(upload.status_code,202,upload.text);short=upload.json()['results'][0]['short']
  self.login();item=self.issue(key)['contributions'][0]
  self.assertEqual(item['author'],'Emily Doe');self.assertEqual(item['role'],'Daughter');self.assertEqual(item['event_date'],'2026-11-01');self.assertEqual(item['original_text'],'Feels dizzy when standing.')
  contribution=self.issue('upload:'+short)['contributions'][0]
  self.assertEqual(contribution['author'],'Morgan Bell');self.assertEqual(contribution['role'],'Registered nurse')
  self.assertEqual(self.client.get(contribution['source_url']).status_code,200)
  self.assertEqual(self.client.post('/api/capture/text',data={'text':'Test','observation_date':'2027-01-01'}).status_code,400)
 def test_documents_expose_release_controls_only_to_clinicians(self):
  public=self.client.get('/api/patient/documents').json()['documents']
  self.assertTrue(all('workflow_issue' not in d for d in public))
  self.login();rows=self.client.get('/api/clinician/document-list').json()['documents']
  document=next(d for d in rows if d['title']=='Electrolyte panel')
  issue=document['workflow_issue'];source=issue['contributions'][0]['id'];key=issue['id']
  self.assertFalse(document['released'])
  body=dict(source=source,recipients=['patient'],message='Please keep this report.',confirmed=True)
  self.assertEqual(self.post(key,'release',**body).status_code,409)
  self.assertEqual(self.post(key,'review',sources=[source]).status_code,200)
  self.assertEqual(self.post(key,'release',**body).status_code,200)
  public=self.client.get('/api/patient/documents').json()['documents'];shared=next(d for d in public if d['id']==document['id'])
  self.assertTrue(shared['accessible']);self.assertEqual(self.client.get(shared['url']).status_code,200)
  self.assertFalse(self.issue(key)['workflow']['communications'])
