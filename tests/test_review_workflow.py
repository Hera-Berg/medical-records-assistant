"""Isolated end-to-end workflow, access-control and audit persistence fixtures."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from agent import device,profile,care_team,ingest
from agent.demo.jane import PROFILE
from agent.demo.calendar_data import CARE_TEAM
from agent.demo.review_dataset import populate,SOURCES
from agent.server import create_app
from agent.clinician_auth import provision,AuthStore
from agent.events import envelope
from agent.server.review_workflow import sources

class ReviewWorkflowTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.temp=Path(temp.name)
  env=patch.dict(os.environ,{device.ENV_CONFIG_HOME:str(self.temp/'machine'),device.ENV_DEVICE:'review-test-device','HEALTH_AGENT_DATA_HOME':str(self.temp/'reader'),'PYTEST_CURRENT_TEST':'review-workflow'})
  env.start();self.addCleanup(env.stop)
  self.root=self.temp/'jane';self.vault=populate(self.root,PROFILE,CARE_TEAM)
  self.passwords=provision(self.root)
  self.app=create_app(self.vault,worker=False);self.app.state.record._clock=lambda:datetime(2026,11,2,12,tzinfo=timezone.utc)
  self.client=TestClient(self.app,base_url='http://127.0.0.1:7777');self.addCleanup(self.client.close)
  self.csrf=None
 def login(self,user='james.smith'):
  r=self.client.post('/api/auth/clinician/login',json={'username':user,'password':self.passwords[user]});self.assertEqual(r.status_code,200,r.text);self.csrf=r.json()['csrf'];return r.json()
 def data(self,date=''):
  r=self.client.get('/api/clinician/dashboard',params={'comparison_date':date});self.assertEqual(r.status_code,200,r.text);return r.json()
 def post(self,key,kind,**body):
  self.count=getattr(self,'count',0)+1
  return self.client.post('/api/clinician/issues/'+key+'/workflow',json={'kind':kind,'request_id':'request-'+str(self.count),**body},headers={'X-Clinician-CSRF':self.csrf or ''})
 def issue(self,key,date=''):return next(i for i in self.data(date)['issues'] if i['id']==key)
 def test_dataset_grouping_dates_accounts_and_sources(self):
  self.assertEqual(sum(not i.get('submission') for i in SOURCES),14)
  self.assertEqual(len(json.loads((self.root/'DATASET-MANIFEST.json').read_text())['sources']),19)
  self.login();d=self.data();self.assertEqual(d['comparison_date'],'2026-10-21');self.assertEqual(d['tab_counts']['reviewed'],0);self.assertEqual(d['tab_counts']['action_required'],0)
  walking=self.issue('walking');self.assertEqual(walking['source_count'],3);self.assertEqual({c['author'] for c in walking['contributions']},{'Morgan Bell','Emily Doe'})
  self.assertEqual(self.issue('dizziness')['source_count'],2)
  self.assertEqual(d['issues'][0]['id'],'critical-sodium');self.assertEqual(d['issues'][0]['priority'],'urgent')
  self.assertEqual(self.issue('low-haemoglobin')['priority'],'untriaged')
  self.assertEqual(self.issue('cardio-recommendation')['contributions'][0]['event_date'],'2026-09-24');self.assertIn('Newly received',self.issue('cardio-recommendation')['label'])
  self.assertEqual(self.data('2026-10-30')['comparison_period']['mode'],'custom')
  self.assertNotIn('walking',{i['id'] for i in self.data('2026-11-01')['issues']})
  for i in d['issues']:
   for c in i['contributions']+i['context']:
    self.assertTrue(c['original_text']);self.assertEqual(self.client.get(c['source_url']).status_code,200)
    if c.get('artifact'):self.assertEqual(self.client.get('/api/clinician/artifact/'+c['artifact']+'/meta').status_code,200)
  self.login('olivia.bennett');self.assertEqual(self.data()['comparison_date'],'2025-03-26')
  self.assertEqual(self.client.get('/api/clinician/dashboard?comparison_date=2027-01-01').status_code,400)
 def test_independent_review_communication_release_and_idempotency(self):
  self.login();key='critical-sodium';issue=self.issue(key);artifact=issue['contributions'][0]['artifact']
  for url in ('/api/artifact/'+artifact,'/api/artifact/'+artifact+'/meta','/api/files','/api/files/content?path=events'):
   self.assertIn(self.client.get(url).status_code,(403,404))
  self.assertNotIn(artifact,{d['id'] for d in self.client.get('/api/documents').json()['documents']})
  self.assertEqual(self.post(key,'release',source=key,recipients=['patient'],message='Please discuss this report.',confirmed=True).status_code,409)
  self.assertEqual(self.post(key,'priority',priority='routine',basis='Fictional clinician assessment after evidence review.').status_code,200)
  self.assertEqual(self.issue(key)['priority'],'routine');self.assertTrue(self.issue(key)['laboratory_flags'])
  self.assertEqual(self.post(key,'communication',recipients=['patient'],message='Report discussed by phone.',method='phone',communication_at='2026-11-02T10:00:00+11:00',confirmed=True).status_code,200)
  self.assertFalse(self.issue(key)['workflow']['reviewed']);self.assertFalse(self.issue(key)['workflow']['releases'])
  self.assertEqual(self.post(key,'review',sources=[key]).status_code,200)
  release=dict(source=key,recipients=['patient','carer'],message='This is the selected report we reviewed.',confirmed=True)
  self.assertEqual(self.post(key,'release',**release).status_code,200)
  duplicate=self.post(key,'release',**release).json();self.assertTrue(duplicate['duplicate'])
  self.assertEqual(len(self.issue(key)['workflow']['releases']),1)
  self.assertEqual(self.client.get('/api/artifact/'+artifact).status_code,200)
  self.assertEqual(self.client.get('/api/artifact/'+artifact+'/meta').status_code,200)
  native=self.client.get('/api/artifact/'+artifact+'/meta').json()['native']
  self.assertEqual(native['patient']['dob'],'1935-11-01');self.assertEqual(native['author'],'Example Pathology Laboratory')
  self.assertEqual(native['results'][0]['reference_interval'],'135–145');self.assertEqual(native['release']['released_by'],'Dr James Smith')
  other=self.issue('low-haemoglobin')['contributions'][0]['artifact'];self.assertEqual(self.client.get('/api/artifact/'+other).status_code,404)
  self.login('olivia.bennett');w=self.issue(key)['workflow'];self.assertTrue(w['reviewed']);self.assertEqual(w['reviews'][0]['actor']['name'],'Dr James Smith');self.assertEqual(self.data()['comparison_date'],'2025-03-26')
  # Recreate server state over the event log: no cache is required for workflow persistence.
  from agent.server.state import RecordState
  fresh=RecordState(self.vault);fresh._clock=lambda:datetime(2026,11,2,12,tzinfo=timezone.utc);self.app.state.record=fresh
  self.assertTrue(self.issue(key)['workflow']['reviewed'])
 def test_requests_acknowledgements_actions_and_old_sources(self):
  self.login();key='critical-sodium'
  payload=dict(recipients=['patient','carer'],timeframe='Within two days',message='Please contact the care team to arrange a discussion of your report.',confirmed=True)
  r=self.post(key,'appointment_request',**payload);self.assertEqual(r.status_code,200,r.text)
  self.assertTrue(self.post(key,'appointment_request',**payload).json()['duplicate'])
  w=self.issue(key)['workflow'];self.assertFalse(w['reviewed']);self.assertFalse(w['releases']);self.assertFalse(w['acknowledgements']);self.assertEqual(len(w['actions']),1)
  feed=self.client.get('/api/shared-care').json();self.assertEqual(len(feed['messages']),1);self.assertNotIn('Sodium',json.dumps(feed));self.assertEqual(feed['documents'],[])
  id=feed['messages'][0]['id'];self.assertEqual(self.client.post('/api/shared-care/acknowledge',json={'message':id,'recipient':'carer'}).status_code,200)
  self.assertEqual(len(self.issue(key)['workflow']['acknowledgements']),1)
  self.assertEqual(self.post(key,'review',sources=[key]).status_code,200)
  self.assertEqual(self.data()['tab_counts']['action_required'],1);self.assertEqual(self.data()['tab_counts']['reviewed'],1)
  old=self.issue(key,'2026-11-01');self.assertTrue(old['contributions'][0]['outside_period']);self.assertTrue(old['workflow']['actions'])
  action=w['actions'][0]['id'];self.assertEqual(self.post(key,'action_resolved',action=action,basis='Fictional team confirmed discussion arranged.').status_code,200)
  self.assertEqual(self.data()['tab_counts']['action_required'],0)
  self.assertEqual(self.issue(key)['workflow']['communications'][0]['delivery'],'Appointment requested; not booked')
 def test_security_unknown_authorship_and_new_uploads(self):
  self.assertEqual(self.post('critical-sodium','review',sources=['critical-sodium']).status_code,401)
  self.login()
  self.assertEqual(self.client.post('/api/clinician/issues/critical-sodium/workflow',json={'kind':'review'}).status_code,403)
  self.assertEqual(self.post('critical-sodium','release',source='low-haemoglobin',recipients=['patient'],message='',confirmed=True).status_code,400)
  self.assertEqual(self.post('critical-sodium','appointment_request',recipients=['unapproved'],timeframe='soon',message='Hello',confirmed=True).status_code,400)
  note=envelope.new('note.recorded',self.vault.identity.id,ts='2026-10-31T22:30:00Z',payload={'source':'typed','text':'Short symptom note'})
  self.app.state.record.append(note);i=self.issue(note.id);c=i['contributions'][0];self.assertEqual(c['author'],'Author not recorded');self.assertIsNone(c['event_date']);self.assertEqual(c['date_basis'],'Observation date not supplied')
  self.assertEqual(self.client.get('/api/clinician/observations/'+note.id).status_code,200)
  r=self.client.post('/api/clinician/capture',files={'files':('new-report.txt',b'New fictional clinician document','text/plain')},headers={'X-Clinician-CSRF':self.csrf});self.assertEqual(r.status_code,202,r.text)
  artifact=r.json()['results'][0]['short']
  self.assertEqual(self.client.get('/api/artifact/'+artifact).status_code,404)
  self.assertEqual(self.client.get('/api/clinician/artifact/'+artifact).status_code,200)
  self.assertEqual(self.client.get('/api/doctor-notes').status_code,200)
  self.client.post('/api/auth/clinician/logout',json={},headers={'X-Clinician-CSRF':self.csrf})
  self.assertEqual(self.client.get('/api/clinician/dashboard').status_code,401);self.assertEqual(self.client.get('/api/doctor-notes').status_code,401)

 def test_internal_actions_do_not_send_or_release_and_remain_after_review(self):
  self.login();key='low-haemoglobin'
  self.assertEqual(self.post(key,'action_created',description='Fictional follow-up assessment recorded by clinician.',timeframe='At next consultation').status_code,200)
  self.assertTrue(self.post(key,'action_created',description='Fictional follow-up assessment recorded by clinician.',timeframe='At next consultation').json()['duplicate'])
  self.assertEqual(self.post(key,'review',sources=[key]).status_code,200)
  self.assertTrue(self.issue(key)['workflow']['reviewed'])
  self.assertEqual(len(self.issue(key)['workflow']['actions']),1)
  self.assertEqual(self.client.get('/api/shared-care').json()['messages'],[])
  self.assertEqual(self.client.get('/api/shared-care').json()['documents'],[])
  self.assertEqual(self.data()['tab_counts']['action_required'],1)
  # Broad public views cannot recover unreleased laboratory findings.
  for path in ('/api/profile','/api/wiki','/api/timeline','/api/documents','/api/artifacts'):
   payload=self.client.get(path)
   self.assertEqual(payload.status_code,200,payload.text)
   self.assertNotIn('122',payload.text)
   self.assertNotIn('Haemoglobin lower',payload.text)

 def test_inapp_send_does_not_imply_acknowledgement_review_or_release(self):
  self.login();key='critical-sodium'
  payload=dict(recipients=['patient'],method='in_app',message='Please contact the care team about your report.',confirmed=True)
  self.assertEqual(self.post(key,'communication',**payload).status_code,200)
  self.assertTrue(self.post(key,'communication',**payload).json()['duplicate'])
  w=self.issue(key)['workflow'];self.assertFalse(w['reviewed']);self.assertFalse(w['releases']);self.assertFalse(w['actions']);self.assertFalse(w['acknowledgements'])
  feed=self.client.get('/api/shared-care').json()
  self.assertEqual(len(feed['messages']),1);self.assertEqual(feed['messages'][0]['status'],'Sent in-app')
  self.assertEqual(feed['messages'][0]['acknowledgements'],[])
