"""Structured chart uses authenticated append-only corrections and original source evidence."""
import unittest
from datetime import datetime,timezone
from tests import test_review_workflow as base
from agent.server.state import RecordState

class ClinicalRecordTests(unittest.TestCase):
 def setUp(self):
  base.ReviewWorkflowTests.setUp(self)
  self.app.state.record._clock=lambda:datetime.now(timezone.utc)
 login=base.ReviewWorkflowTests.login
 def chart(self):
  r=self.client.get('/api/clinician/clinical-record');self.assertEqual(r.status_code,200,r.text);return r.json()
 def write(self,row,updates=None,**changes):
  from agent.server.clinical_record import FIELDS
  fields={k:str(row.get(k) or '') for k in FIELDS[row['kind']]};fields.update(updates or {})
  return self.client.post('/api/clinician/clinical-record',json={'id':row['id'],'kind':row['kind'],'revision':row['revision'],'values':fields,'source_ids':[],'basis':'Corrected after reviewing the original source.','request_id':'test-'+str(len(self.vault.read().events)),**changes},headers={'X-Clinician-CSRF':self.csrf})
 def test_source_dates_classification_unknown_fields_and_indications(self):
  self.assertEqual(self.client.get('/api/clinician/clinical-record').status_code,401)
  self.login();data=self.chart();self.assertEqual(len(data['sections']['history']),3);self.assertEqual(len(data['sections']['medications']),4)
  af=next(r for r in data['sections']['history'] if r['name']=='Atrial fibrillation');self.assertEqual(af['first_documented'],'2025-05-02')
  self.assertEqual({m['name'] for m in af['related_medicines']},{'Apixaban','Metoprolol'})
  hypertension=next(r for r in data['sections']['history'] if r['name']=='Hypertension');self.assertEqual(hypertension['first_documented'],'2024-11-01');self.assertFalse(hypertension['related_medicines'])
  allergy=data['sections']['allergies'][0];self.assertEqual(allergy['verification'],'reported');self.assertIn('rash',allergy['reaction'])
  for name in ('Continence','Cognition'):
   row=next(r for r in data['sections']['function'] if r['name']==name);self.assertFalse(row['description']);self.assertIsNone(row['first_documented'])
  for kind in data['sections'].values():
   for row in kind:
    for source in row['sources']:self.assertEqual(self.client.get(source['url']).status_code,200,source['url'])
  self.assertTrue(all(not r['prescriptions'] for r in data['sections']['medications']))
 def test_edits_persist_audit_and_shared_projection(self):
  self.app.state.record._clock=lambda:datetime.now(timezone.utc)
  self.login();data=self.chart();med=next(r for r in data['sections']['medications'] if r['name']=='Apixaban')
  original=self.client.get(med['sources'][0]['url']).content
  response=self.write(med,{'route':'Oral','description':'Medication chart reconciled; no new prescription issued.'});self.assertEqual(response.status_code,200,response.text)
  changed=next(r for r in self.chart()['sections']['medications'] if r['id']==med['id']);self.assertEqual(changed['route'],'Oral')
  audit=changed['history'][-1];self.assertEqual(audit['actor']['name'],'Dr James Smith');self.assertEqual(audit['previous']['route'],'');self.assertEqual(audit['values']['route'],'Oral')
  detail=self.client.get('/api/clinician/wiki/'+med['id']).json();self.assertIn('Oral',str(detail))
  self.assertEqual(self.client.get(med['sources'][0]['url']).content,original)
  self.assertEqual(self.write(med,{'route':'Oral'}).status_code,409)
  # New conditions use problem subjects, never medication or prescription subjects.
  body={'kind':'history','values':{'name':'Fictional test condition','status':'past','first_documented':'','description':'An isolated test entry, not Jane active demo data.','record_type':'condition'},'source_ids':[],'basis':'Isolated test fixture.','request_id':'new-condition'}
  r=self.client.post('/api/clinician/clinical-record',json=body,headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(r.status_code,200,r.text)
  self.assertTrue(r.json()['id'].startswith('problem:'))
  row=next(r for r in self.chart()['sections']['history'] if r['name']=='Fictional test condition')
  self.assertEqual(row['status'],'past');self.assertIsNone(row['first_documented'])
  self.assertFalse(any(r['name']=='Fictional test condition' for r in self.chart()['sections']['medications']))
  fresh=RecordState(self.vault);self.app.state.record=fresh
  self.login('olivia.bennett');row=next(r for r in self.chart()['sections']['medications'] if r['name']=='Apixaban');self.assertEqual(row['route'],'Oral');self.assertEqual(row['history'][-1]['actor']['name'],'Dr James Smith')
  self.assertEqual(self.write(row,{'description':'Locum reconciliation recorded.'}).status_code,200)
  self.assertEqual(next(r for r in self.chart()['sections']['medications'] if r['name']=='Apixaban')['history'][-1]['actor']['name'],'Dr Olivia Bennett')
  # Private structured edits do not unlock original clinical documents or patient views.
  self.assertNotIn('Locum reconciliation recorded.',str(self.client.get('/api/profile').json()))
 def test_allergy_function_validation_and_csrf(self):
  self.login();data=self.chart();allergy=data['sections']['allergies'][0]
  r=self.write(allergy,{'verification':'verified','reaction':'Reported itchy rash; clinician reviewed the history.'});self.assertEqual(r.status_code,200,r.text)
  row=self.chart()['sections']['allergies'][0];self.assertEqual(row['verification'],'verified')
  function=next(r for r in self.chart()['sections']['function'] if r['name']=='Mobility')
  r=self.write(function,{'description':'Uses a frame for usual mobility.','function_basis':'Usual function'});self.assertEqual(r.status_code,200,r.text)
  row=next(r for r in self.chart()['sections']['function'] if r['name']=='Mobility');self.assertEqual(row['description'],'Uses a frame for usual mobility.')
  self.assertIn('Uses a frame for usual mobility.',self.client.get('/api/clinician/profile').json()['values']['accessibility'])
  self.assertEqual(self.write(row,{'first_documented':'2099-01-01'}).status_code,400)
  self.assertEqual(self.client.post('/api/clinician/clinical-record',json={}).status_code,403)
  self.assertEqual(self.write(row,basis='').status_code,400)
 def test_add_medicine_and_allergy_and_keep_supply_distinct(self):
  self.login()
  cases=[('medications',{'name':'Fictional fixture medicine','dose':'1 tablet','route':'Oral','frequency':'Once daily','status':'active','first_documented':'','description':'Isolated fixture entry, not a prescription.'}),('allergies',{'name':'Fictional fixture substance','reaction':'Reported itching','verification':'reported','first_documented':'','description':'Unverified isolated test report.'})]
  for kind,values in cases:
   body={'kind':kind,'values':values,'source_ids':[],'basis':'Isolated fixture test.','request_id':'add-'+kind}
   response=self.client.post('/api/clinician/clinical-record',json=body,headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(response.status_code,200,response.text)
   row=next(r for r in self.chart()['sections'][kind] if r['id']==response.json()['id'])
   self.assertIsNone(row['first_documented']);self.assertEqual(row['history'][-1]['actor']['name'],'Dr James Smith')
   repeat=self.client.post('/api/clinician/clinical-record',json=body,headers={'X-Clinician-CSRF':self.csrf});self.assertTrue(repeat.json()['duplicate']);self.assertEqual(repeat.json()['id'],row['id'])
   if kind=='medications':
    self.assertFalse(row['prescriptions']);self.assertEqual(row['supply'],'Supply remaining not recorded')
    self.assertEqual(self.write(row,{'status':'stopped'}).status_code,200)
    current=self.client.get('/api/clinician/wiki').json()['current_medications'];self.assertNotIn(row['id'],{m['id'] for m in current})
    self.assertEqual(next(r for r in self.chart()['sections'][kind] if r['id']==row['id'])['status'],'stopped')
   else:self.assertEqual(row['verification'],'reported')

 def test_profile_dash_note_does_not_duplicate_structured_allergy(self):
  from agent.events import envelope
  from agent import profile
  self.login();row=self.chart()['sections']['allergies'][0]
  self.assertEqual(self.write(row,{'name':'Penicillin','reaction':'Rash'}).status_code,200)
  self.vault.append(envelope.new(profile.UPDATED,self.vault.identity.id,payload={'values':{'allergy_notes':'Penicillin — rash.'}}));self.app.state.record.invalidate()
  allergies=self.chart()['sections']['allergies']
  self.assertEqual([a['name'] for a in allergies],['Penicillin'])
  self.assertEqual(allergies[0]['reaction'],'Rash')
