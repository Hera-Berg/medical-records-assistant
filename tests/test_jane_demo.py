"""End-to-end Jane scenario checks, runnable without pytest or an AI model."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from agent import device, ingest, profile, projection
from agent.demo import _identity
from agent.demo.jane import seed, PROFILE, MARKER, source_text, STATIN
from agent.demo.jane_data import HISTORY
from agent.demo_metadata import SCENARIO, entries
from agent.errors import HealthAgentError
from agent.events import envelope
from agent.extract import jobs, runner
from agent.server import create_app
from agent.runtime import supervisor
from agent.vault import Vault

ROOT = Path(__file__).resolve().parents[1]
# Independent date manifest from the requested scenario, including both 25 August entries.
DATES = '''2024-10-24 2024-10-25 2024-10-28 2024-11-11 2024-11-13 2024-11-20 2024-12-04 2024-12-18
2025-01-15 2025-02-04 2025-03-18 2025-04-02 2025-04-09 2025-04-11 2025-04-14 2025-04-24 2025-05-02 2025-05-06 2025-05-13 2025-06-05 2025-06-20 2025-07-16 2025-07-23 2025-07-24 2025-08-12 2025-09-03 2025-09-19 2025-10-07 2025-11-02 2025-11-03 2025-11-05 2025-11-07 2025-11-14 2025-12-09 2025-12-18
2026-01-12 2026-02-05 2026-02-12 2026-03-04 2026-03-19 2026-04-10 2026-04-28 2026-05-05 2026-05-20 2026-06-09 2026-06-16 2026-07-08 2026-07-13 2026-07-20 2026-07-30 2026-08-17 2026-08-20 2026-08-25 2026-08-25 2026-08-26 2026-08-28 2026-09-02 2026-09-10 2026-09-24 2026-09-30 2026-10-08 2026-10-12 2026-10-15 2026-10-18 2026-10-19 2026-10-20 2026-10-21 2026-10-22'''.split()


class JaneDemoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        env = patch.dict(os.environ, {device.ENV_CONFIG_HOME:str(self.temp/'machine'),
                         'HEALTH_AGENT_DATA_HOME':str(self.temp/'reader'), 'PYTEST_CURRENT_TEST':'jane-demo'})
        env.start(); self.addCleanup(env.stop)
        self.root = self.temp/'jane'
        seed(self.root)
        self.vault = Vault.open(self.root, identity=_identity())
        previous = supervisor.install(None)
        def restore():
            current = supervisor.install(previous)
            if current: current.shutdown()
        self.addCleanup(restore)
        self.app = create_app(self.vault, worker=False)
        self.client = TestClient(self.app, base_url='http://127.0.0.1:7777')
        self.addCleanup(self.client.close)

    def get(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, (path,r.text[:100]))
        return r

    def test_complete_manifest_and_same_latest_five(self):
        rows = self.get('/api/timeline').json()['rows']
        self.assertEqual(len(rows),68)
        self.assertEqual([r['date']['iso'] for r in rows],sorted(DATES,reverse=True))
        self.assertEqual({r['demo']['title'] for r in rows},{r['title'] for r in HISTORY})
        self.assertEqual(self.get('/api/timeline?limit=5').json()['rows'],rows[:5])
        self.assertIn('/api/timeline?limit=5',self.get('/overview.js').text)
        for r in rows:
            self.assertEqual(r['date_kind'],'occurred')
            self.assertTrue(r['demo']['fictional'])
            self.assertIn('Fictional demo',r['text'])
            self.assertNotEqual(r['demo']['added_at'][:10],r['date']['iso'])
            self.assertTrue(r['demo']['author'])
            self.assertTrue(r['citation']['resolved'])

    def test_every_source_and_related_link_resolves_with_original_wording(self):
        documents = self.get('/api/documents').json()['documents']
        self.assertEqual(len(documents),68)
        self.assertEqual(len({d['id'] for d in documents}),68)
        metadata = entries(self.vault.read().events,self.app.state.record.snapshot().artifacts)
        for item in HISTORY:
            note = metadata[item['id']].payload
            short = note['artifact']
            pdf = self.get('/api/artifact/'+short)
            self.assertTrue(pdf.content.startswith(b'%PDF-1.4'))
            self.assertEqual(pdf.headers['x-content-type-options'],'nosniff')
            self.get('/artifact/'+short)
            meta = self.get('/api/artifact/'+short+'/meta').json()
            self.assertTrue(meta['present'])
            self.assertFalse(meta['is_recording'])
            self.assertEqual(meta['artifact_ts'][:10],item['date'])
            self.assertEqual(meta['reading']['state'],'demo-authored')
            self.assertIn('not live AI',meta['reading']['text'])
            original = self.get('/api/artifact/'+meta['demo']['original_wording_artifact']).content.decode('utf8')
            self.assertEqual(original,source_text(item))
            self.assertIn(item['text'],original)
            for related in meta['demo']['related']:
                self.assertIn(related['artifact'],[d['id'] for d in documents])
                self.get('/api/artifact/'+related['artifact'])
        for doc in documents:
            self.assertTrue(doc['demo'])
            self.assertTrue(doc['professional'])
            self.assertIsNotNone(doc['document_date'])

    def test_profile_and_latest_medications_agree_with_sources(self):
        data = self.get('/api/profile').json()
        self.assertEqual(data['values'],{**{k:'' for k in profile.FIELDS},**PROFILE})
        self.assertEqual(data['values']['date_of_birth'],'1935-01-01')
        self.assertIn('Patient-reported; not clinician-verified',data['values']['allergy_notes'])
        self.assertEqual(data['clinical']['allergies'],[])
        meds = data['clinical']['medications']
        self.assertEqual({m['name']:m['dose'] for m in meds},STATIN)
        self.assertTrue(all(not m['conflicted'] for m in meds))
        entity = self.app.state.record.snapshot().projection.entities['med:amlodipine']
        self.assertEqual(entity.slots['dose'].winner.value.literal,'2.5 mg once daily')
        self.assertIn('5 mg once daily',[c.value.literal for c in entity.slots['dose'].superseded])
        self.assertEqual(len(data['clinical']['problems']),3)
        for item in HISTORY:
            if item['date']=='2026-08-20':
                self.assertIn('Amlodipine: 5 mg once daily.',source_text(item))
            if item['date']=='2026-08-28':
                self.assertIn('Amlodipine: 2.5 mg once daily.',source_text(item))

    def test_rerun_is_noop_and_preserves_user_edits_uploads_settings(self):
        before = len(self.vault.read().events)
        self.assertEqual(seed(self.root).events,0)
        self.assertEqual(len(self.vault.read().events),before)
        self.vault.append(envelope.new(profile.UPDATED,self.vault.identity.id,
            payload={'values':{'preferred_name':'My Jane','language':''}}))
        self.vault.append(envelope.new('note.recorded',self.vault.identity.id,
            payload={'text':'User addition remains untouched'}))
        upload = ingest.ingest_bytes(self.vault,b'User uploaded original - retain me',
                    ingest.CaptureContext(original_filename='my-added-note.txt'))
        config = self.root/'config.toml'
        config.write_text(config.read_text()+'\n# User setting kept\n')
        before = self.vault.read().events
        original_bytes = (self.root/upload.rel).read_bytes()
        self.assertEqual(seed(self.root).events,0)
        self.assertEqual(self.vault.read().events,before)
        self.assertEqual((self.root/upload.rel).read_bytes(),original_bytes)
        self.assertIn('# User setting kept',config.read_text())
        values = profile.read(before)['values']
        self.assertEqual(values['preferred_name'],'My Jane')
        self.assertEqual(values['language'],'')
        self.assertEqual(len(entries(before,self.app.state.record.snapshot().artifacts)),68)

    def test_authored_fixture_updates_keep_prior_sources_without_duplicate_events(self):
        metadata=entries(self.vault.read().events,self.app.state.record.snapshot().artifacts)
        old=metadata['jane-history-21'].payload['artifact']
        from copy import deepcopy
        changed=deepcopy(HISTORY)
        changed[20]['text'] += ' Additional authored demonstration context.'
        with patch('agent.demo.jane.HISTORY',changed):
            seed(self.root)
            self.assertEqual(seed(self.root).events,0)
        rows=self.get('/api/timeline').json()['rows']
        self.assertEqual(len(rows),68)
        current=next(r for r in rows if r['demo']['title']=='Personal note - medication routine')
        self.assertNotEqual(current['citation']['artifact'],old)
        self.get('/api/artifact/'+old)
        meta=self.get('/api/artifact/'+current['citation']['artifact']+'/meta').json()
        self.assertIn(old,[r['artifact'] for r in meta['demo']['related']])
        self.assertEqual(self.get('/api/documents').json()['total'],68)
        self.assertEqual(runner.enqueue_unread(self.vault,jobs.Queue.open(self.root/'.agent')),())

    def test_refuses_real_vaults_and_unrelated_demo_folders_without_writes(self):
        other = self.temp/'real'
        other.mkdir()
        (other/'patient-record.txt').write_text('Keep actual user data')
        with self.assertRaises(HealthAgentError): seed(other)
        self.assertEqual(list(other.iterdir()),[other/'patient-record.txt'])
        (other/'DEMO-DATA.md').write_text('An older, different demonstration')
        with self.assertRaises(HealthAgentError): seed(other)
        self.assertEqual((other/'patient-record.txt').read_text(),'Keep actual user data')
        empty = self.temp/'ordinary'; empty.mkdir()
        # An ordinary empty vault projection is not automatically seeded.
        self.assertEqual(projection.project([]).rows,())

    def test_private_notes_and_unknown_sources_remain_excluded(self):
        secret='PRIVATE-DOCTOR-COMMENT-NEVER-RELEASED'
        self.vault.append(envelope.new('compartment.note.sealed',self.vault.identity.id,
            payload={'ciphertext':secret,'title':secret,'date':'2026-10-22'}))
        for url in ('/api/timeline','/api/timeline?limit=5','/api/timeline/months','/api/documents','/api/profile'):
            self.assertNotIn(secret,self.get(url).text)
        self.assertEqual(self.client.get('/api/artifact/does-not-exist').status_code,404)
        self.assertEqual(self.client.get('/api/artifact/does-not-exist/meta').status_code,404)
        self.assertEqual(self.client.get('/api/documents',headers={'host':'untrusted.example'}).status_code,421)
        self.assertEqual(self.client.put('/api/profile',json={},headers={'origin':'https://untrusted.example'}).status_code,403)
        self.assertEqual(len(self.get('/api/timeline').json()['rows']),68)

    def test_filters_and_calendar_use_same_events(self):
        rows=self.get('/api/timeline').json()['rows']
        for tier in ('patient-reported','prescriber-issued','lab-issued','artefact'):
            actual=self.get('/api/timeline?tier='+tier).json()['rows']
            self.assertEqual(actual,[r for r in rows if r['marker']==tier])
            self.assertTrue(actual)
        month_data=self.get('/api/timeline/months').json()['months']
        self.assertEqual(sum(m['rows'] for m in month_data),68)
        self.assertEqual(month_data[0]['month'],'2026-10')
        for month in month_data:
            prefix=month['month']
            # Calendar uses genuine month bounds, including February.
            import calendar
            year,num=map(int,prefix.split('-'))
            last=calendar.monthrange(year,num)[1]
            actual=self.get(f'/api/timeline?from={prefix}-01&to={prefix}-{last}').json()['rows']
            self.assertEqual(actual,[r for r in rows if r['month']==prefix])
        august=self.get('/api/timeline?from=2026-08-25&to=2026-08-25').json()['rows']
        self.assertEqual(len(august),2)
        nursing = next(r for r in august if r['demo']['author'].startswith('Morgan'))
        self.assertIn("Jane reported dizziness", nursing['text'])
        self.assertIn('not an established medication cause', self.get('/api/artifact/'+nursing['demo']['original_wording_artifact']).text)

    def test_preloaded_sources_are_not_queued_for_ai_but_user_uploads_are(self):
        queue=jobs.Queue.open(self.root/'.agent')
        self.assertEqual(runner.enqueue_unread(self.vault,queue),())
        added=ingest.ingest_bytes(self.vault,b'New user source needing review',
                    ingest.CaptureContext(original_filename='new.txt'))
        pending=runner.enqueue_unread(self.vault,queue)
        self.assertEqual([job.artifact for job in pending],[added.short])

    def test_replay_is_deterministic_and_read_routes_do_not_write_events(self):
        events=self.vault.read().events
        as_of='2026-10-23T00:00:00Z'
        first=projection.project(events,as_of)
        self.assertEqual(first.files,projection.project(reversed(events),as_of).files)
        self.assertEqual(first.rows,projection.project(events,as_of).rows)
        for route in ('/overview','/timeline','/documents','/profile','/api/profile','/api/timeline','/api/documents'):
            self.get(route)
        self.assertEqual(self.vault.read().events,events)
        self.assertEqual(first.anomalies,())

    def test_pdf_legibility_bounds_and_matching_existing_reports(self):
        import pdfplumber
        metadata=entries(self.vault.read().events,self.app.state.record.snapshot().artifacts)
        checked=0
        for item in HISTORY:
            short=metadata[item['id']].payload['artifact']
            data=self.get('/api/artifact/'+short).content
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    self.assertTrue(page.extract_text())
                    for ch in page.chars:
                        self.assertGreaterEqual(ch['x0'],40)
                        self.assertLessEqual(ch['x1'],555)
                        self.assertGreaterEqual(ch['top'],40)
                        self.assertLessEqual(ch['bottom'],810)
                text='\n'.join(p.extract_text() for p in pdf.pages)
                self.assertIn('FICTIONAL DEMONSTRATION',text)
                if item['date']=='2026-10-08':
                    self.assertIn('132',text)
                    self.assertIn('4.8',text)
                    self.assertIn('x10^9/L',text)
                    self.assertNotIn('?109',text)
                if item['source_type']=='Demo voice-note transcript':
                    self.assertIn('No audio recording exists',text)
            checked+=1
        self.assertEqual(checked,68)

    def test_served_js_syntax_and_demo_ui_rendering(self):
        jsc=Path('/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc')
        if not jsc.is_file(): self.skipTest('JavaScriptCore unavailable')
        index=self.get('/').text
        import re
        bundle=re.search(r'src="(/assets/[^\"]+\.js)"',index).group(1)
        source=self.get(bundle).text
        self.assertIn('r.demo.title',source)
        # Remove only the final ES module exports (if any) to parse as a script.
        syntax=subprocess.run([str(jsc),'-e','new Function('+json.dumps(source)+'); print("parsed");'],text=True,capture_output=True)
        self.assertEqual(syntax.returncode,0,syntax.stdout+syntax.stderr)
        overview=(ROOT/'frontend/public/overview.js').read_text()
        rows=self.get('/api/timeline?limit=5').json()
        profile_data=self.get('/api/profile').json()
        script='''let Widget; globalThis.HTMLElement=class{};globalThis.customElements={define(n,c){Widget=c;}};'''+overview+'''
const view=new Widget(); view.profile='''+json.dumps(profile_data)+''';view.timeline='''+json.dumps(rows)+''';view.render();
const assert=(c,m)=>{if(!c)throw Error(m);};
assert((view.timelineRows().match(/<li>/g)||[]).length===5,'five events');
assert(view.innerHTML.includes('FICTIONAL JANE DOE DEMONSTRATION'),'demo heading');
for(const row of view.timeline.rows)assert(view.innerHTML.includes('/artifact/'+row.citation.artifact),'real source');
assert(view.innerHTML.includes('Questions saved for the next appointment'),'newest event');
assert(!view.innerHTML.includes('<audio'),'no fake audio');print('demo overview passed');'''
        result=subprocess.run([str(jsc),'-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
