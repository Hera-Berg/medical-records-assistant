"""Chronological, source-based feed, independent of extracted condition facts."""
import unittest
from tests import test_review_workflow as base
from agent.events import envelope

class ClinicalTimelineTests(unittest.TestCase):
 setUp=base.ReviewWorkflowTests.setUp
 login=base.ReviewWorkflowTests.login
 def feed(self):
  response=self.client.get('/api/clinician/timeline/feed');self.assertEqual(response.status_code,200,response.text);return response.json()
 def test_sources_classification_and_access(self):
  self.assertEqual(self.client.get('/api/clinician/timeline/feed').status_code,401)
  self.login();data=self.feed();rows=data['events']
  self.assertEqual(len(rows),19);self.assertEqual(len({r['id'] for r in rows}),len(rows))
  dates=[r['date'] for r in rows if r['date']];self.assertEqual(dates,sorted(dates,reverse=True))
  medication=[r for r in rows if r['category']=='Medication changes']
  self.assertEqual({r['title'] for r in medication},{'GP prescription record for atrial fibrillation','Current medication chart'})
  cardio=next(r for r in rows if r['title']=='Cardiology assessment: atrial fibrillation');self.assertEqual(cardio['category'],'Letters')
  letter=next(r for r in rows if r['title']=='Cardiologist recommends medication review')
  self.assertEqual(letter['date'],'2026-10-24');self.assertEqual(letter['document_date'],'2026-09-24');self.assertEqual(letter['event_type'],'Letter received')
  consultation=next(r for r in rows if r['title']=='Completed usual GP consultation');self.assertEqual(consultation['date'],'2026-10-21T09:30:00+11:00')
  self.assertIn('Electrolyte panel',{r['title'] for r in rows if r['category']=='Test results'})
  nurse=next(r for r in rows if r['title']=='Nursing record of current support');self.assertEqual(nurse['category'],'Nursing/GP notes');self.assertIn('Jane requested',nurse['excerpt'])
  carer=next(r for r in rows if r['author']=='Emily Doe');self.assertTrue(carer['excerpt'].startswith('On our walk'));self.assertEqual(carer['event_type'],'Carer update')
  for row in rows:
   self.assertEqual(self.client.get(row['source_url']).status_code,200,row['source_url'])
   self.assertEqual(self.client.get(row['original_url']).status_code,200,row['original_url'])
  for r in rows:
   if r['original_url'].startswith('/api/'):self.assertEqual(self.client.get(r['original_url'].replace('/api/clinician/','/api/')).status_code,404 if r['category'] not in ('Patient & carer updates',) and r['title']!='Walking support requested' else 200)
  self.login('olivia.bennett');self.assertEqual(self.feed()['events'],rows)
 def test_undated_authorship_and_sealed_notes_are_not_inferred(self):
  self.vault.append(envelope.new('note.recorded',self.vault.identity.id,payload={'source':'typed','text':'A brief genuine symptom note.'}))
  self.vault.append(envelope.new('clinician.note.sealed',self.vault.identity.id,payload={'ciphertext':'PRIVATE-SECRET'}))
  self.app.state.record.invalidate();self.login();data=self.feed()
  row=next(r for r in data['events'] if r.get('excerpt')=='A brief genuine symptom note.')
  self.assertEqual(row['author'],'Author not recorded');self.assertIn('Observation date not supplied',row['date_basis'])
  self.assertNotIn('PRIVATE-SECRET',str(data))
