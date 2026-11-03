"""Fictional specialist additions preserve originals and review/release boundaries."""
import io
import unittest
from datetime import datetime,timezone
import pdfplumber
from agent.events import envelope
from agent.demo.calendar_data import CARE_TEAM
from tests import test_review_workflow as base
from agent.demo.specialist_letters import upgrade
from agent.demo_metadata import entries

class SpecialistLettersTests(unittest.TestCase):
 setUp=base.ReviewWorkflowTests.setUp
 login=base.ReviewWorkflowTests.login
 data=base.ReviewWorkflowTests.data
 post=base.ReviewWorkflowTests.post
 issue=base.ReviewWorkflowTests.issue
 def test_full_letters_originals_and_new_review_required(self):
  self.vault.append(envelope.new('note.recorded',self.vault.identity.id,payload={'demo_scenario':'jane-two-year-v1','care_team':[*CARE_TEAM,{'name':'Daniel Brooks','role':'Podiatrist','organisation':'Example Community Health'},{'name':'Olivia Reed','role':'Audiologist','organisation':'Example Community Audiology'}]}))
  self.app.state.record.invalidate()
  self.app.state.record._clock=lambda:datetime.now(timezone.utc)
  self.login();old=entries(self.vault.read().events,self.app.state.record.snapshot().artifacts)['cardio-recommendation']
  old_artifact=old.payload['artifact'];old_pdf=self.client.get('/api/clinician/artifact/'+old_artifact).content
  self.assertEqual(self.post('cardio-recommendation','review',sources=['cardio-recommendation']).status_code,200)
  self.assertEqual(self.post('cardio-recommendation','release',source='cardio-recommendation',recipients=['patient'],message='Earlier letter',confirmed=True).status_code,200)
  added=upgrade(self.vault);self.assertEqual(len(added),3);self.app.state.record.invalidate()
  self.assertEqual(self.client.get('/api/clinician/artifact/'+old_artifact).content,old_pdf)
  rows=entries(self.app.state.record.snapshot().events,self.app.state.record.snapshot().artifacts)
  for edition in added:
   key=edition['id'];p=rows[key].payload
   response=self.client.get('/api/clinician/artifact/'+edition['artifact']);self.assertEqual(response.status_code,200)
   with pdfplumber.open(io.BytesIO(response.content)) as pdf:
    text='\n'.join(page.extract_text() or '' for page in pdf.pages)
    self.assertIn('FICTIONAL SPECIALIST CORRESPONDENCE',text);self.assertIn('Jane Doe',text);self.assertIn('1 November 1935',text);self.assertIn(p['author'],text)
    if key=='cardio-recommendation':
     self.assertGreaterEqual(len(pdf.pages),2)
     for medicine in ('Apixaban 5 mg','Metoprolol 25 mg','Amlodipine 2.5 mg','Rosuvastatin 5 mg'):self.assertIn(medicine,text)
     self.assertIn('2026-09-24',text)
   issue=self.issue(key);self.assertFalse(issue['workflow']['reviewed']);self.assertFalse(issue['workflow']['releases']);self.assertFalse(issue['workflow']['communications'])
   self.assertEqual(self.client.get('/api/artifact/'+edition['artifact']).status_code,404)
  new=rows['cardio-recommendation'].payload
  self.assertEqual(new['previous_source_dates']['document_date'],'2026-09-24');self.assertIn(old_artifact,new['previous_artifacts'])
  self.assertEqual(self.post('cardio-recommendation','release',source='cardio-recommendation',recipients=['patient'],message='New letter',confirmed=True).status_code,409)
  self.assertEqual(upgrade(self.vault),[])
  self.assertEqual(len(self.data()['issues']),len({i['id'] for i in self.data()['issues']}))
