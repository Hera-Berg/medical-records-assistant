"""Rendered facility editions, coherent new encounters and preserved access/audit boundaries."""
import io,json
import unittest
from pathlib import Path
from datetime import datetime,timezone
import pdfplumber
from tests import test_review_workflow as base
from agent.demo.specialist_letters import upgrade as add_specialists
from agent.demo.document_editions import upgrade,prepare
from agent.demo.document_templates import FOOTER
from agent.demo_metadata import entries
from agent.demo.calendar_data import CARE_TEAM
from agent.events import envelope

class DocumentEditionsTests(unittest.TestCase):
 setUp=base.ReviewWorkflowTests.setUp
 login=base.ReviewWorkflowTests.login
 data=base.ReviewWorkflowTests.data
 post=base.ReviewWorkflowTests.post
 issue=base.ReviewWorkflowTests.issue
 def install_members(self):
  self.vault.append(envelope.new('note.recorded',self.vault.identity.id,payload={'demo_scenario':'jane-two-year-v1','care_team':[*CARE_TEAM,{'name':'Daniel Brooks','role':'Podiatrist','organisation':'Example Community Health'},{'name':'Olivia Reed','role':'Audiologist','organisation':'Example Community Audiology'}]}))
  add_specialists(self.vault);self.app.state.record.invalidate();self.app.state.record._clock=lambda:datetime.now(timezone.utc)
 def test_all_pdf_pages_titles_and_new_encounter_dates(self):
  self.install_members();self.login();before=entries(self.app.state.record.snapshot().events,self.app.state.record.snapshot().artifacts)
  previous_results=before['critical-sodium'].payload['native_document']['results']
  result=upgrade(self.vault,export=self.temp/'exports');self.assertEqual(len(result['editions']),16);self.assertTrue(Path(result['archive']).is_dir())
  self.app.state.record.invalidate();current=entries(self.app.state.record.snapshot().events,self.app.state.record.snapshot().artifacts)
  for edition in result['editions']:
   p=current[edition['id']].payload;response=self.client.get('/api/clinician/artifact/'+p['artifact']);self.assertEqual(response.status_code,200,response.text)
   if p['artifact']!=edition['previous_artifact']:self.assertIn(edition['previous_artifact'],p['previous_artifacts'])
   with pdfplumber.open(io.BytesIO(response.content)) as pdf:
    self.assertLessEqual(len(pdf.pages),2)
    texts=[]
    for page in pdf.pages:
     text=page.extract_text() or '';texts.append(text)
     self.assertEqual(text.count(FOOTER),1)
     for banned in ('Earlier correspondence retained','Source records','Release information not recorded','Fictional author attribution','extraction','application review'):
      self.assertNotIn(banned,text)
     for word in page.extract_words():self.assertGreaterEqual(word['x0'],40);self.assertLessEqual(word['x1'],555)
    full='\n'.join(texts);self.assertIn('Jane Doe',full);self.assertIn('1 November 1935',full);self.assertIn('DEMO-JD-19351101',full)
    if edition['id'] in ('cardio-recommendation','audiology-correspondence','podiatry-correspondence','physio'):
     self.assertIn('Dear Dr Smith,',full);self.assertIn(p['author'],full)
     self.assertEqual(p['event_date'],datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Australia/Sydney')).date().isoformat())
     self.assertTrue(p['fictional_encounter']['added_details'])
     if p['role']!='Cardiologist':self.assertNotIn('Dr '+p['author'],full)
    else:
     self.assertEqual(p['event_date'],before[edition['id']].payload['event_date'])
     self.assertEqual(p['native_document']['date'],before[edition['id']].payload['native_document']['date'])
   meta=self.client.get('/api/clinician/artifact/'+p['artifact']+'/meta').json()
   self.assertTrue(meta['native']['original_document_layout']);self.assertEqual(meta['native']['title'],p['title'])
   self.assertIn(p['title']+'.pdf',response.headers['content-disposition'])
   if edition['id'] in ('cardio-recommendation','audiology-correspondence','low-haemoglobin'):
    page=self.client.get('/api/clinician/artifact/'+p['artifact']+'/pages/1');self.assertEqual(page.status_code,200,page.text[:100] if page.status_code!=200 else '')
    self.assertTrue(page.content.startswith(b'\x89PNG'));self.assertEqual(self.client.get('/api/artifact/'+p['artifact']+'/pages/1').status_code,404)
  self.assertEqual(current['critical-sodium'].payload['native_document']['results'],previous_results)
  cardio=Path(next(e['export_path'] for e in result['editions'] if e['id']=='cardio-recommendation'))
  with pdfplumber.open(cardio) as pdf:
   text='\n'.join(p.extract_text() for p in pdf.pages)
   for medicine in ('Apixaban','Metoprolol','Amlodipine','Rosuvastatin'):self.assertIn(medicine,text)
  from concurrent.futures import ThreadPoolExecutor
  source=current['cardio-recommendation'].payload['artifact']
  with ThreadPoolExecutor(max_workers=2) as executor:
   pages=list(executor.map(lambda n:self.client.get('/api/clinician/artifact/'+source+'/pages/'+str(n)),[1,2]))
  self.assertTrue(all(page.status_code==200 and page.content.startswith(b'\x89PNG') for page in pages))
  self.assertEqual(upgrade(self.vault)['editions'],[])
  self.assertEqual(self.data()['comparison_date'],'2026-10-21');self.login('olivia.bennett');self.assertEqual(self.data()['comparison_date'],'2025-03-26')
 def test_review_history_and_exact_release_survive_without_releasing_new_bytes(self):
  self.install_members();self.login();key='critical-sodium';old=self.issue(key)['contributions'][0]['artifact'];old_bytes=self.client.get('/api/clinician/artifact/'+old).content
  self.post(key,'review',sources=[key]);self.post(key,'release',source=key,recipients=['patient'],message='Earlier report shared.',confirmed=True)
  result=upgrade(self.vault);self.app.state.record.invalidate();new=self.issue(key)['contributions'][0]['artifact'];self.assertNotEqual(new,old)
  self.assertEqual(self.client.get('/api/artifact/'+old).content,old_bytes);self.assertEqual(self.client.get('/api/artifact/'+new).status_code,404);self.assertEqual(self.client.get('/api/artifact/'+new+'/pages/1').status_code,404)
  state=self.issue(key)['workflow'];self.assertFalse(state['reviewed']);self.assertFalse(state['releases']);self.assertTrue(any(h['kind']=='release' for h in state['history']))
  public=self.client.get('/api/patient/documents').json()['documents'];row=next(d for d in public if d['title']=='Electrolyte panel')
  self.assertEqual(row['url'],'/artifact/'+old);self.assertEqual(row['status'],'Released earlier edition')
  self.assertEqual(self.client.post('/api/clinician/issues/'+key+'/workflow',json={'kind':'release','source':key,'recipients':['patient'],'message':'New report','confirmed':True,'request_id':'not-reviewed'},headers={'X-Clinician-CSRF':self.csrf}).status_code,409)
  self.post(key,'review',sources=[key]);self.post(key,'release',source=key,recipients=['patient'],message='Current report shared.',confirmed=True)
  row=next(d for d in self.client.get('/api/patient/documents').json()['documents'] if d['title']=='Electrolyte panel');self.assertEqual(row['url'],'/artifact/'+new);self.assertEqual(self.client.get('/api/artifact/'+new+'/pages/1').status_code,200)
