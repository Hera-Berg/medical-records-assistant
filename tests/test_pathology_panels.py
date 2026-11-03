"""Conventional fictional reports; older bytes and review/release boundaries survive upgrades."""
import io
from datetime import datetime,timezone
from unittest.mock import patch
import pdfplumber
from tests import test_review_workflow as workflow_base
from agent.demo.pathology_reports import enrich,report_pdf,upgrade
from agent.demo.review_dataset import SOURCES
from agent.demo_metadata import entries
from agent.events import envelope
from agent import ingest

class PathologyPanelTests(workflow_base.ReviewWorkflowTests):
 def test_pdf_panels_have_complete_tables_and_preserved_key_values(self):
  self.login()
  data=self.data();panel=next(i for i in data['issues'] if i['id']=='critical-sodium')
  row=panel['contributions'][0]
  self.assertEqual(row['ordered_panel'],'Electrolyte panel')
  self.assertEqual([r['test'] for r in row['results']],['Sodium','Potassium','Chloride','Bicarbonate','Calcium','Magnesium','Phosphate'])
  self.assertEqual(row['results'][0]['result'],'122');self.assertEqual(row['results'][1]['result'],'4.3')
  self.assertTrue(all(r['unit'] and r['reference'] for r in row['results']))
  self.assertEqual(len(next(i for i in data['issues'] if i['id']=='low-haemoglobin')['contributions'][0]['results']),14)
  for item in SOURCES:
   if not item.get('ordered_panel'):continue
   pdf=report_pdf(item,'2026-11-03T00:00:00Z')
   with pdfplumber.open(io.BytesIO(pdf)) as document:
    self.assertEqual(len(document.pages),1)
    text=document.pages[0].extract_text()
    for label in (item['title'],'Result','Reference interval','Jane Doe','Received:', 'FICTIONAL'):
     self.assertIn(label,text)
    for result in item['results']:self.assertIn(result[0],text)
    # Final result row remains above the report footer, including the long historical panel.
    last=[w for w in document.pages[0].extract_words() if w['text']==item['results'][-1][0].split()[0]]
    self.assertTrue(last)
    self.assertLess(last[-1]['bottom'],740)

 def test_upgrade_retains_original_bytes_and_requires_fresh_approval(self):
  self.login();key='critical-sodium';state=self.app.state.record
  # An earlier single-analyte fictional edition is an isolated fixture, not a change to Jane's history.
  current=entries(state.snapshot().events,state.snapshot().artifacts)[key].payload
  p=dict(current);p.pop('pathology_format',None);p.pop('ordered_panel',None)
  native=dict(p['native_document']);native.pop('ordered_panel',None);native['results']=native['results'][:1];p['native_document']=native
  result=ingest.ingest_bytes(self.vault,b'Older single-analyte original',ingest.CaptureContext(source='import',original_filename='old-source.txt'))
  p['artifact']=result.short
  state.append(envelope.new('note.recorded',self.vault.identity.id,ts='2026-11-02T10:00:00Z',payload=p))
  # Put this fixture edition after the seed timestamp while keeping all subsequent acts later still.
  state._clock=lambda:datetime(2026,11,4,12,tzinfo=timezone.utc)
  fixture=envelope.new('note.recorded',self.vault.identity.id,ts='2026-11-04T10:00:00Z',payload=p);state.append(fixture)
  self.assertEqual(self.post(key,'review',sources=[key]).status_code,200)
  self.assertEqual(self.post(key,'release',source=key,recipients=['patient'],message='Earlier report',confirmed=True).status_code,200)
  self.assertEqual(self.post(key,'communication',recipients=['patient'],message='Earlier report discussed',method='phone',communication_at='2026-11-04T11:00:00Z',confirmed=True).status_code,200)
  before={path:path.read_bytes() for path in self.vault.events_dir.glob('*.jsonl')}
  original=self.client.get('/api/artifact/'+result.short).content
  with patch('agent.demo.pathology_reports.envelope.now_ts',return_value='2026-11-04T13:00:00Z'):
   added=upgrade(self.vault)
  state.invalidate();state._clock=lambda:datetime(2026,11,4,14,tzinfo=timezone.utc)
  self.assertEqual(len(added),1)
  for path,content in before.items():self.assertTrue(path.read_bytes().startswith(content))
  self.assertEqual(self.client.get('/api/artifact/'+result.short).content,original)
  revised=self.issue(key);new=revised['contributions'][0]['artifact']
  self.assertNotEqual(new,result.short)
  self.assertFalse(revised['workflow']['reviewed']);self.assertEqual(revised['workflow']['releases'],[]);self.assertEqual(revised['workflow']['communications'],[])
  self.assertEqual(self.client.get('/api/artifact/'+new).status_code,404)
  self.assertEqual(self.post(key,'release',source=key,recipients=['patient'],message='Current report',confirmed=True).status_code,409)
  self.assertEqual(self.post(key,'review',sources=[key]).status_code,200)
  self.assertTrue(self.issue(key)['workflow']['reviewed'])
  self.assertEqual(self.post(key,'release',source=key,recipients=['patient'],message='Current report',confirmed=True).status_code,200)
  self.assertEqual(self.client.get('/api/artifact/'+new).status_code,200)
  self.assertEqual(len(self.issue(key)['workflow']['reviews']),2)
  self.assertEqual(len(self.issue(key)['workflow']['history']),5)
  self.assertEqual(upgrade(self.vault),[])
