"""Readable source, release and prescription tests; no AI or browser dependencies."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import unittest

from tests import test_jane_demo as base
from agent.demo.jane import seed
from agent.demo_metadata import entries
from agent.events import envelope
from agent.prescriptions import expiry_status, listing
from agent.demo.documents import page_pdf
from agent import ingest


class NativeDocumentTests(unittest.TestCase):
    setUp=base.JaneDemoTests.setUp
    get=base.JaneDemoTests.get

    def test_all_demo_sources_are_native_and_have_full_content(self):
        docs=self.get('/api/documents').json()['documents']
        self.assertEqual(len(docs),68)
        metadata=[]
        for doc in docs:
            meta=self.get('/api/artifact/'+doc['id']+'/meta').json()
            native=meta['native']; metadata.append(meta)
            self.assertTrue(native['sections'])
            self.assertEqual(native['patient']['dob'],'1935-01-01')
            self.assertEqual(native['author'],doc['professional'])
            body=' '.join(s['text'] for s in native['sections'])
            self.assertNotIn('Matching original example content',body)
            self.assertNotIn('Patient-visible authored source; not live AI extraction',body)
            if native.get('contribution'):
                self.assertIsNone(native['release'])
                self.assertIn('Added by',doc['release_note'])
            else:
                self.assertIn('Released for viewing by '+native['release']['released_by'],doc['release_note'])
            self.assertTrue(self.get('/api/artifact/'+doc['id']).content.startswith(b'%PDF'))
        cardio=next(m['native'] for m in metadata if m['demo']['event_date']=='2026-09-24')
        self.assertEqual(cardio['author'],'Dr Matthew Thompson')
        self.assertEqual(cardio['recipient'],'Dr James Smith, Sydney Aged Care')
        self.assertEqual(cardio['release']['released_by'],'Dr James Smith')
        self.assertGreater(len(' '.join(s['text'] for s in cardio['sections']).split()),300)
        self.assertTrue(any(s['heading']=='Yours sincerely' for s in cardio['sections']))
        pathology=next(m['native'] for m in metadata if m['demo']['event_date']=='2026-10-08')
        self.assertGreater(len(pathology['results']),10)
        self.assertEqual(next(r['result'] for r in pathology['results'] if 'Haemoglobin' in r['test']),'132')

    def test_source_ids_originals_and_user_notes_survive_idempotent_updates(self):
        before=self.vault.read().events
        snapshot=self.app.state.record.snapshot()
        original={s:(self.root/a.rel).read_bytes() for s,a in snapshot.artifacts.items()}
        self.vault.append(envelope.new('note.recorded',self.vault.identity.id,payload={'text':'User-created note'}))
        self.assertEqual(seed(self.root).events,0)
        self.assertEqual(len(self.get('/api/timeline').json()['rows']),69)
        for short,data in original.items():
            self.assertEqual(self.get('/api/artifact/'+short).content,data)
        self.assertEqual(len(entries(self.vault.read().events,self.app.state.record.snapshot().artifacts)),68)

    def test_recorded_release_identity_is_preserved_separately(self):
        snapshot=self.app.state.record.snapshot()
        event=next(e for e in entries(snapshot.events,snapshot.artifacts).values() if e.payload['event_date']=='2026-09-24')
        payload=copy.deepcopy(event.payload)
        payload['native_document']['release']={'released_by':'Dr Olivia Example','date':'2026-09-26','simulated':True}
        self.vault.append(envelope.new('note.recorded',self.vault.identity.id,payload=payload))
        self.assertEqual(seed(self.root).events,0)
        meta=self.get('/api/artifact/'+payload['artifact']+'/meta').json()['native']
        self.assertEqual(meta['release']['released_by'],'Dr Olivia Example')
        row=next(d for d in self.get('/api/documents').json()['documents'] if d['id']==payload['artifact'])
        self.assertEqual(row['release_note'],'Released for viewing by Dr Olivia Example')
        self.assertEqual(row['release_date'],'2026-09-26')
        self.assertEqual(meta['author'],'Dr Matthew Thompson')

    def test_prescriptions_repeat_balances_and_sources_agree(self):
        rows=self.get('/api/prescriptions').json()['prescriptions']
        self.assertEqual(len(rows),8)
        self.assertEqual(len({r['id'] for r in rows}),8)
        meds={m['name']:m['dose'] for m in self.get('/api/profile').json()['clinical']['medications']}
        for row in rows:
            native=self.get('/api/artifact/'+row['source']+'/meta').json()['native']
            source=next(r for r in native['prescriptions'] if r['id']==row['id'])
            self.assertEqual(row,source)
            self.assertEqual(row['dose_per_administration'],'1 tablet ('+row['strength']+')')
            self.assertIsNone(row['estimated_supply_until'])
            self.assertEqual(row['supply_label'],'Supply remaining not recorded')
            if row['frequency']=='twice daily':
                self.assertIsNone(row['timing'])
                self.assertNotIn('morning',row['plain_directions'])
            if row['treatment_status']=='current': self.assertEqual(row['original_directions'],meds[row['medicine']])
        blue=next(r for r in rows if r['id']=='2026-08-28:amlodipine')
        self.assertEqual(blue['expiry_date'],'2026-10-31')
        self.assertEqual(blue['repeats_remaining'],0)
        self.assertEqual(blue['repeat_label'],'No repeats remaining')
        self.assertEqual(blue['original_repeats'],2)
        self.get('/api/artifact/'+blue['repeat_update']['source'])
        self.assertEqual(blue['treatment_status'],'current')
        # Changing the clock never consumes unrecorded repeats or medicine supply.
        snapshot=self.app.state.record.snapshot()
        future=listing(snapshot.events,snapshot.artifacts,datetime(2027,10,23,tzinfo=timezone.utc))
        rosuvastatin=next(r for r in future if r['id']=='2026-08-28:rosuvastatin')
        self.assertIsNone(rosuvastatin['repeats_remaining'])
        self.assertEqual(rosuvastatin['treatment_status'],'current')
        self.assertEqual(rosuvastatin['expiry']['state'],'expired')

    def test_expiry_sydney_boundaries_unknown_and_prn(self):
        before=datetime(2026,10,31,12,59,tzinfo=timezone.utc)
        after=datetime(2026,10,31,13,0,tzinfo=timezone.utc)
        self.assertEqual(expiry_status('2026-10-31',before)['label'],'Expires today')
        self.assertEqual(expiry_status('2026-10-31',after)['state'],'expired')
        today=before.astimezone(__import__('zoneinfo').ZoneInfo('Australia/Sydney')).date()
        self.assertEqual(expiry_status((today+timedelta(days=30)).isoformat(),before)['state'],'soon')
        self.assertEqual(expiry_status((today+timedelta(days=31)).isoformat(),before)['state'],'valid')
        self.assertEqual(expiry_status(None,before)['label'],'Expiry not recorded')
        self.assertEqual(expiry_status('bad-date',before)['state'],'unknown')
        snapshot=self.app.state.record.snapshot()
        event=next(e for e in entries(snapshot.events,snapshot.artifacts).values() if e.payload['event_date']=='2026-08-28')
        payload=copy.deepcopy(event.payload)
        rx=payload['native_document']['prescriptions'][1]
        rx.update(prn=True,original_directions='As needed',frequency='as needed',expiry_date=None,
                  dispensing_date='2026-10-01',estimated_supply_until='2026-11-01')
        altered=envelope.new('note.recorded',self.vault.identity.id,payload=payload)
        rows=listing([*snapshot.events,altered],snapshot.artifacts,before)
        row=next(r for r in rows if r['id']==rx['id'])
        self.assertIsNone(row['estimated_supply_until'])
        self.assertEqual(row['expiry']['state'],'unknown')
        self.assertIsNone(row['repeats_remaining'])

    def test_uploaded_pdf_gets_text_without_invented_release(self):
        result=ingest.ingest_bytes(self.vault,page_pdf([['Actual uploaded text: <script>not executable</script>','Second line exactly as supplied.']]),
            ingest.CaptureContext(original_filename='My uploaded letter.pdf'))
        meta=self.get('/api/artifact/'+result.short+'/meta').json()
        native=meta['native']
        self.assertTrue(native['extracted'])
        self.assertIsNone(native['release'])
        self.assertNotIn('fictional',native)
        self.assertIn('Second line exactly as supplied.',native['sections'][0]['text'])
        self.assertNotIn('Dr James Smith',json.dumps(native))
        self.assertEqual(self.client.get('/api/artifact/private-note/meta').status_code,404)

    def test_appointment_sheet_agrees_and_native_rendering_is_safe(self):
        response=self.client.post('/api/summary/preview',json={'question':'Review medicines','label':'GP'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('31 October 2026',response.text)
        self.assertIn('No repeats remaining',response.text)
        self.assertIn('Supply remaining not recorded',response.text)
        rx=self.get('/api/prescriptions').json()['prescriptions']
        docs=self.get('/api/documents').json()['documents']
        meta=[self.get('/api/artifact/'+d['id']+'/meta').json() for d in docs]
        jsc=Path('/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc')
        if not jsc.exists(): self.skipTest('JavaScript runtime unavailable')
        for name in ('native-documents.js','native-documents.css'):
            self.assertEqual(self.get('/'+name).content,(base.ROOT/'frontend/public'/name).read_bytes())
        script='globalThis.HTMLElement=class{};globalThis.customElements={define(){}};\n'+(base.ROOT/'frontend/public/native-documents.js').read_text()
        script+='\nconst docs='+json.dumps(meta)+';const rx='+json.dumps(rx)+''';
const assert=(v,m)=>{if(!v)throw Error(m);};
for(const doc of docs){const html=healthDocumentRendering.documentPage(doc);assert(html.includes('Back to Documents'),'back link');assert(!html.includes('<object')&&!html.includes('<iframe'),'native reading default');assert(html.includes('Fictional demo'),'demo label');assert(html.includes('Download original'),'original retained');}
const card=healthDocumentRendering.prescription(rx.find(r=>r.id==='2026-08-28:amlodipine'));
assert(card.includes('31 October 2026')&&card.includes('No repeats remaining'),'expiry and repeat balance');
const unsafe=JSON.parse(JSON.stringify(docs[0]));unsafe.native.title='<script>attack()</script>';unsafe.native.sections=[{heading:'<img onerror=x>',text:'<script>attack()</script>'}];
const html=healthDocumentRendering.documentPage(unsafe);assert(!html.includes('<script>')&&!html.includes('<img'),'safe stored text');
const unknown=healthDocumentRendering.prescription({medicine:'Unknown',expiry:{state:'unknown',label:'Expiry not recorded'}});assert(unknown.includes('Not recorded'),'unknown stays unknown');print('Native document rendering checks passed');'''
        path=self.temp/'native-test.js';path.write_text(script)
        run=subprocess.run([str(jsc),str(path)],capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
