"""Documents metadata and the illustrative library remain distinct."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent import config, device
from agent.demo.authoring import ingested
from agent.server.routes.documents import router
from agent.server.state import RecordState
from agent.vault import Vault, scaffold

ROOT=Path(__file__).resolve().parents[1]


class DocumentsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        env=patch.dict(os.environ,{device.ENV_CONFIG_HOME:str(root/'machine')})
        env.start();self.addCleanup(env.stop)
        scaffold(root/'record')
        (root/'record'/config.CONFIG_FILENAME).write_text('sync_profile = "local"\nport = 7777\nlocale = "en"\n')
        self.vault=Vault.open(root/'record',identity=device.issue(label='documents-test'))
        self.state=RecordState(self.vault)
        app=FastAPI();app.state.record=self.state;app.include_router(router)
        self.client=TestClient(app);self.addCleanup(self.client.close)

    def test_examples_are_not_real_records_and_reads_write_nothing(self):
        before=self.vault.read().events
        response=self.client.get('/api/documents')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),{'documents':[],'total':0})
        self.assertEqual(before,self.vault.read().events)
        for key in ('Jane Doe','Dr James Smith','Dr Matthew Thompson'):
            self.assertNotIn(key,response.text)

    def test_original_names_and_date_provenance_are_preserved(self):
        older=ingested(self.vault.identity.id,'abc123',ts='2026-10-10T00:00:00Z',mime='application/pdf',artifact_ts='2026-08-14T00:00:00Z')
        older.payload['capture']['original_filename']='C:\\Downloads\\My pathology report.pdf'
        newer=ingested(self.vault.identity.id,'def456',ts='2026-10-12T00:00:00Z',mime='application/pdf')
        newer.payload['capture']['original_filename']='Referral letter.pdf'
        self.state.append(older);self.state.append(newer)
        before=self.vault.read().events
        data=self.client.get('/api/documents').json()
        self.assertEqual(data['total'],2)
        first,second=data['documents']
        self.assertEqual(first['title'],'Referral letter.pdf')
        self.assertIsNone(first['document_date'])
        self.assertEqual(first['added_date'],'2026-10-12')
        self.assertEqual(second['title'],'My pathology report.pdf')
        self.assertEqual(second['document_date'],'2026-08-14')
        self.assertEqual(second['category'],'PDF document')
        self.assertFalse(first['example'])
        self.assertNotIn('release_note',first)
        self.assertEqual(before,self.vault.read().events)

    def test_library_has_requested_correspondence_and_distinct_blood_dates(self):
        samples=json.loads((ROOT/'frontend/public/document-examples.json').read_text())
        self.assertTrue(samples['example'])
        self.assertEqual(len(samples['documents']),12)
        self.assertEqual(len({doc['id'] for doc in samples['documents']}),12)
        for doc in samples['documents']:
            self.assertEqual(doc['recipient'],'Dr James Smith')
            self.assertEqual(doc['patient'],'Jane Doe')
            self.assertTrue(doc['professional'])
            self.assertTrue(doc['sections'])
            self.assertEqual(doc['release_note'],'Released for viewing by Dr James Smith')
        panels=[doc for doc in samples['documents'] if 'including lipids' in doc['title']]
        self.assertEqual(len({doc['date'] for doc in panels}),4)
        self.assertTrue(all(doc['results'] for doc in panels))

    def test_document_ui_filters_previews_and_keeps_real_metadata_honest(self):
        jsc=Path('/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc')
        runtime=shutil.which('node') or (str(jsc) if jsc.is_file() else None)
        if not runtime:self.skipTest('JavaScript runtime required')
        data=(ROOT/'frontend/public/document-examples.json').read_text()
        source=(ROOT/'frontend/public/documents.js').read_text()
        prelude='''let Widget;globalThis.HTMLElement=class {};globalThis.customElements={define(name,ctor){Widget=ctor;}};
const assert=(v,m)=>{if(!v)throw Error(m);};const output=typeof print==='function'?print:console.log;
'''
        checks=r'''
const view=new Widget();view.examples=samples;view.uploads={documents:[],total:0};view.source='examples';view.query='';view.category='';view.sort='newest';
view.query='lipids';assert(view.filtered().length===4,'four dated lipid panels');
view.query='August';assert(view.filtered().length===1,'search readable month');
view.query='Dr Matthew Thompson';assert(view.filtered().length===1,'search professional');
view.query='';view.category='Allied health';assert(view.filtered().length===2,'filter document type');
view.category='';view.sort='oldest';assert(view.filtered()[0].date==='2026-02-12','oldest first');
let row=view.row(samples.documents[0]);assert(row.includes('To Dr James Smith · Re Jane Doe'),'recipient and patient');
assert(row.includes('Released for viewing by Dr James Smith'),'sharing note');
const results={innerHTML:''},count={textContent:''};let opened=false;
const dialog={innerHTML:'',showModal(){opened=true;},querySelector(){return{focus(){}};}};
view.querySelector=selector=>selector==='dialog'?dialog:selector==='.docs-results'?results:count;
view.render();assert(view.innerHTML.includes('Browse record folder'),'original folder browser retained');
view.open('blood-panel-2026-10-08');assert(opened&&dialog.innerHTML.includes('LDL cholesterol'),'full report preview with results');
assert(dialog.innerHTML.includes('Illustrative document for a fictional patient'),'preview labelled as example');
assert(dialog.innerHTML.includes('Download example (.txt)'),'example download');
view.source='uploads';row=view.row({id:'abc123',title:'<img src=x>',category:'PDF document',document_date:null,added_date:'2026-10-18'});
assert(!row.includes('<img')&&row.includes('&lt;img'),'uploaded names escaped');
assert(row.includes('Added to record')&&!row.includes('Released for viewing by Dr James Smith'),'no invented date or doctor release on uploads');
view.renderResults();assert(results.innerHTML.includes('No uploads yet'),'honest empty state');
view.uploads=null;view.renderResults();assert(results.innerHTML.includes('could not be loaded')&&!results.innerHTML.includes('No uploads yet'),'failure is not an empty record');
output('Documents UI checks passed');
'''
        result=subprocess.run([runtime,'-e',prelude+'const samples='+data+';'+source+checks],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Documents UI checks passed',result.stdout)

    def test_shipped_document_assets_match_sources(self):
        for name in ('documents.js','documents.css','document-examples.json'):
            self.assertEqual((ROOT/'frontend/public'/name).read_bytes(),(ROOT/'agent/server/static_files'/name).read_bytes())
        bundle=next((ROOT/'agent/server/static_files/assets').glob('*.js')).read_text()
        self.assertIn('to:`/documents`,label:`Documents`',bundle)
        self.assertIn('x===`documents`?(0,b.jsx)(`health-documents`',bundle)


if __name__=='__main__':unittest.main()
