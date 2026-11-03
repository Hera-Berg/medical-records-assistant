"""Shared patient/carer portal: real local booking records, demo scheduling and safe documents."""
import unittest
from datetime import datetime,timezone
from tests import test_review_workflow as base
from agent.server.state import RecordState
from agent.events import envelope

class PatientPortalTests(unittest.TestCase):
 setUp=base.ReviewWorkflowTests.setUp
 login=base.ReviewWorkflowTests.login
 data=base.ReviewWorkflowTests.data
 post=base.ReviewWorkflowTests.post
 issue=base.ReviewWorkflowTests.issue

 def request(self,key='critical-sodium',recipients=None):
  self.login()
  r=self.post(key,'appointment_request',recipients=recipients or ['patient','carer'],timeframe='Within two days',message='Please arrange a discussion with the care team about your report.',confirmed=True)
  self.assertEqual(r.status_code,200,r.text)
  return self.client.get('/api/shared-care').json()['messages'][-1]['id']
 def book(self,request,slot,**changes):
  return self.client.post('/api/patient/appointments/book',json={'request':request,'slot':slot,'recipient':'patient','confirmed':True,'request_id':'portal-booking',**changes})
 def test_booking_adds_one_calendar_entry_without_clinical_side_effects(self):
  request=self.request();slots=self.client.get('/api/patient/appointments/availability').json()
  self.assertTrue(slots['fictional']);self.assertEqual(slots['timezone'],'Australia/Sydney')
  self.assertGreater(len(slots['slots']),0)
  slot=slots['slots'][0]
  self.assertTrue(all(s['professional']=='Dr James Smith' for s in slots['slots']))
  before=len(self.vault.read().events)
  response=self.book(request,slot['id']);self.assertEqual(response.status_code,200,response.text)
  booking=response.json()['booking'];self.assertEqual(len(self.vault.read().events),before+1)
  event=next(e for e in self.vault.read().events if e.id==booking['event']);self.assertIn('calendar_entry',event.payload);self.assertIn('patient_booking',event.payload)
  calendar=self.client.get('/api/calendar').json()['events'];entry=next(e for e in calendar if e['id']==booking['id'])
  self.assertEqual(entry['date'],slot['date']);self.assertEqual(entry['time'],slot['time']);self.assertEqual(entry['status'],'Booked')
  self.assertNotIn('122',entry['description'])
  feed=self.client.get('/api/shared-care').json();self.assertEqual(feed['messages'][0]['booking']['id'],booking['id']);self.assertEqual(feed['messages'][0]['acknowledgements'],[])
  state=self.issue('critical-sodium')['workflow'];self.assertFalse(state['reviewed']);self.assertFalse(state['releases']);self.assertFalse(state['acknowledgements']);self.assertFalse(state['actions'][0]['resolved']);self.assertEqual(state['actions'][0]['booking']['id'],booking['id'])
  self.assertEqual(self.data()['comparison_date'],'2026-10-21')
  fresh=RecordState(self.vault);fresh._clock=lambda:datetime(2026,11,2,12,tzinfo=timezone.utc);self.app.state.record=fresh
  self.assertEqual(self.client.get('/api/shared-care').json()['messages'][0]['booking']['id'],booking['id'])
  self.login('olivia.bennett');self.assertEqual(self.data()['comparison_date'],'2025-03-26')
 def test_duplicates_invalid_and_conflicting_slots(self):
  request=self.request(recipients=['patient']);slot=self.client.get('/api/patient/appointments/availability').json()['slots'][0]['id']
  self.assertEqual(self.book(request,slot,confirmed=False).status_code,400)
  self.assertEqual(self.book(request,slot,recipient='carer').status_code,400)
  self.assertEqual(self.book(request,'invented-slot').status_code,409)
  self.assertEqual(self.book('unknown',slot).status_code,404)
  first=self.book(request,slot);self.assertEqual(first.status_code,200,first.text);before=len(self.vault.read().events)
  repeat=self.book(request,slot);self.assertTrue(repeat.json()['duplicate']);self.assertEqual(len(self.vault.read().events),before)
  self.assertNotIn(slot,{s['id'] for s in self.client.get('/api/patient/appointments/availability').json()['slots']})
  second=self.request('low-haemoglobin');self.assertEqual(self.book(second,slot).status_code,409)
  closed=self.post('low-haemoglobin','action_resolved',action=second,basis='Request closed by clinician.');self.assertEqual(closed.status_code,200)
  other=self.client.get('/api/patient/appointments/availability').json()['slots'][0]['id'];self.assertEqual(self.book(second,other).status_code,409)
 def test_ten_document_opening_list_and_backend_release_permissions(self):
  payload=self.client.get('/api/patient/documents').json()
  self.assertEqual(payload['initial_limit'],10);self.assertGreaterEqual(payload['total'],10)
  first=payload['documents'][:10];titles={d['title'] for d in first}
  self.assertIn('Electrolyte panel',titles);self.assertIn('Full blood examination',titles);self.assertIn('Dr Matthew Thompson - Cardiologist letter',titles)
  self.assertTrue(all(not d['accessible'] and d['url'] is None for d in first))
  self.assertNotIn('122 mmol',str(payload));self.assertNotIn('result',first[0])
  self.assertEqual(self.client.get('/api/clinician/document-list').status_code,401)
  self.login();self.assertTrue(all(d['accessible'] for d in self.client.get('/api/clinician/document-list').json()['documents']))
  artifact=self.issue('critical-sodium')['contributions'][0]['artifact'];self.assertEqual(self.client.get('/api/artifact/'+artifact).status_code,404)
  self.assertEqual(self.post('critical-sodium','review',sources=['critical-sodium']).status_code,200)
  self.assertEqual(self.post('critical-sodium','release',source='critical-sodium',recipients=['patient'],message='Approved report.',confirmed=True).status_code,200)
  doc=next(d for d in self.client.get('/api/patient/documents').json()['documents'] if d['title']=='Electrolyte panel')
  self.assertTrue(doc['accessible']);self.assertEqual(doc['url'],'/artifact/'+artifact);self.assertEqual(self.client.get('/api/artifact/'+artifact).status_code,200)
 def test_own_corrections_preserve_original_and_reject_clinical_records(self):
  notes=self.client.get('/api/patient/updates').json()['notes'];note=next(n for n in notes if n['id']=='patient-dizzy')
  original=note['original_text']
  r=self.client.post('/api/patient/updates/patient-dizzy/correct',json={'text':'Corrected patient wording.','revision':note['revision']});self.assertEqual(r.status_code,200,r.text)
  amended=next(n for n in self.client.get('/api/patient/updates').json()['notes'] if n['id']=='patient-dizzy');self.assertEqual(amended['text'],'Corrected patient wording.');self.assertEqual(amended['original_text'],original)
  self.assertEqual(self.client.post('/api/patient/updates/patient-dizzy/correct',json={'text':'Conflicting edit.','revision':note['revision']}).status_code,409)
  before=len(self.vault.read().events);self.assertTrue(self.client.post('/api/patient/updates/patient-dizzy/correct',json={'text':amended['text'],'revision':note['revision']}).json()['duplicate']);self.assertEqual(len(self.vault.read().events),before)
  for id in ('critical-sodium','staff-walk','nurse-clinical','unknown'):
   self.assertEqual(self.client.post('/api/patient/updates/'+id+'/correct',json={'text':'Bad correction','revision':'invalid'}).status_code,403)
  recorded=self.client.post('/api/capture/text',data={'text':'My own submitted note.'});self.assertEqual(recorded.status_code,202)
  self.assertIn(recorded.json()['event'],{n['id'] for n in self.client.get('/api/patient/updates').json()['notes']})
  self.login();private=self.client.post('/api/clinician/capture/text',data={'text':'Private clinician submitted update.'},headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(private.status_code,202)
  self.assertNotIn(private.json()['event'],{n['id'] for n in self.client.get('/api/patient/updates').json()['notes']})
  self.assertNotIn('Private clinician submitted update.',self.client.get('/api/timeline').text)
  self.assertEqual(self.client.post('/api/patient/updates/'+private.json()['event']+'/correct',json={'text':'No','revision':'x'}).status_code,403)
 def test_clinical_confirmation_is_authenticated_and_shared_booking_origin_is_checked(self):
  self.assertEqual(self.client.get('/api/review').status_code,401)
  self.assertEqual(self.client.get('/review',follow_redirects=False).status_code,303)
  self.login();self.assertEqual(self.client.get('/api/clinician/review').status_code,200)
  self.assertEqual(self.client.post('/api/clinician/review/missing',json={'action':'confirm'}).status_code,403)
  self.assertEqual(self.client.post('/api/patient/appointments/book',json={},headers={'Origin':'https://other.invalid'}).status_code,403)
