"""Exercise the overview's rendering, read failures and cancellation without a model."""
from pathlib import Path
import json
import tempfile
import unittest

from tests import js_runtime

ROOT = Path(__file__).resolve().parents[1]


class OverviewTests(unittest.TestCase):
    def test_live_data_empty_states_escaping_and_partial_failures(self):
        if not js_runtime.runtime():
            self.skipTest('A JavaScript runtime is needed for the overview UI check')
        source = (ROOT / 'frontend/public/overview.js').read_text()
        prelude = r'''
let Overview;
globalThis.HTMLElement = class { addEventListener() {} removeEventListener() {} };
globalThis.customElements = {define(name, ctor) {Overview = ctor;}};
globalThis.AbortController = class {constructor() {this.signal={aborted:false};} abort() {this.signal.aborted=true;} };
const assert = (condition, message) => {if (!condition) throw Error(message);};
const output = typeof print === 'function' ? print : console.log;
'''
        checks = r'''
const profile={values:{preferred_name:'Alex',full_name:'Alex Example',gp_name:'Dr Example',gp_practice:'Example practice'},care_team:[{name:'Dr Example',role:'Usual GP',organisation:'Example practice'}],clinical:{medications:[{id:'med:test',name:'Test medication',dose:'Recorded dose',stale:true},{id:'med:conflict',name:'Conflicted medicine',dose:'NEVER DISPLAY THIS DOSE',conflicted:true}],allergies:[{id:'allergy:test'}]},review_count:2};
const timeline={rows:[{subject_id:'med:test',marker:'prescriber-issued',date:{render:'around June 2026'},date_label:'document dated',text:'Recorded fact',citation:{resolved:true,artifact:'abc123'}}]};
const view=new Overview();view.profile=profile;view.timeline=timeline;view.render();
assert(view.innerHTML.includes('Alex'),'personal greeting');
assert(view.innerHTML.includes('around June 2026 · document dated'),'date precision and provenance preserved');
assert(view.innerHTML.includes('Recorded fact'),'real timeline content');
assert(view.innerHTML.includes('Needs confirming'),'stale medication visible');
assert(view.innerHTML.includes('Conflicting entries') && !view.innerHTML.includes('NEVER DISPLAY THIS DOSE'),'conflicted dose withheld');
assert(view.innerHTML.includes('Dr Example'),'actual care team');
for (const path of ['/profile','/timeline','/record','/add','/settings','/care-team']) assert(view.innerHTML.includes('href="'+path+'"'),'working destination '+path);
assert(!view.innerHTML.includes('24 October'),'no invented appointment');
assert(!view.innerHTML.includes('Manage access'),'no unsupported sharing action');
profile.values.preferred_name='<img src=x onerror=alert(1)>';
timeline.rows[0].text='<script>unsafe()</script>';
view.render();assert(!view.innerHTML.includes('<img') && !view.innerHTML.includes('<script>'),'patient content is text');
view.profile={values:{},clinical:{medications:[],allergies:[]},review_count:0};view.timeline={rows:[]};view.render();
assert(view.innerHTML.includes('Your story starts here'),'empty timeline');
assert(view.innerHTML.includes('No allergies or reactions recorded') && !/no known allerg|NKDA/i.test(view.innerHTML),'empty does not mean no allergies');
assert(!view.innerHTML.includes('Good morning, Alex'),'no invented identity');
view.profile=null;view.timeline=null;view.render();
assert(view.innerHTML.includes('temporarily unavailable'),'failure feedback');
assert(!view.innerHTML.includes('No medications confirmed yet'),'failed retrieval is not an empty record');
(async()=>{
 let calls=[];
 globalThis.fetch=async (url,init)=>{calls.push([url,init]);if(url.startsWith('/api/timeline'))throw Error('offline');return {ok:true,json:async()=>profile};};
 view.isConnected=true;await view.load();
 assert(view.innerHTML.includes('Dr Example') && view.innerHTML.includes('timeline is temporarily unavailable'),'partial failure preserves successful panel');
 assert(calls.length===2 && calls.every(call=>!call[1].method && call[1].cache==='no-store'),'only same-origin uncached reads');
 let pending=[];
 fetch=()=>new Promise(resolve=>pending.push(resolve));
 const loading=view.load();view.disconnectedCallback();view.isConnected=false;
 for(const resolve of pending)resolve({ok:true,json:async()=>profile});
 await loading;
 assert(view.innerHTML.includes('Opening your overview'),'detached screen never rerenders');
 output('Overview UI checks passed');
})().catch(error=>{output(error.stack);throw error;});
'''
        with tempfile.TemporaryDirectory() as directory:
            completed = js_runtime.run(prelude + source + checks, Path(directory))
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        self.assertIn('Overview UI checks passed',completed.stdout)

    def test_shipped_assets_match_sources_and_existing_tabs_are_preserved(self):
        static=ROOT/'agent/server/static_files'
        for asset in ('overview.js','overview.css','appearance.js','appearance.css','patient-profile.js','patient-profile.css'):
            self.assertEqual((static/asset).read_bytes(),(ROOT/'frontend/public'/asset).read_bytes(),asset)
        index=(static/'index.html').read_text()
        self.assertIn('/overview.js',index)
        self.assertIn('/overview.css',index)
        source=(ROOT/'frontend/src/components/Sidebar.tsx').read_text()
        bundle=js_runtime.shipped_bundle()
        # Both workspaces: the patient and carer's, and the signed-in clinician's.
        for label in ('Home','Record an update','Notifications','Calendar','Documents','My care team','Timeline','Ask your record','My profile','Settings',
                      'Review dashboard','Clinical record','Progress notes','Care team','Upload or record information','Ask the patient record','Patient profile'):
            self.assertIn(label,source)
            self.assertIn(label,bundle)
        self.assertIn('first === "overview"',(ROOT/'frontend/src/App.tsx').read_text())
        self.assertTrue(js_runtime.routes_to(bundle,'overview','health-overview'))
        self.assertTrue(js_runtime.routes_to(bundle,'profile','patient-profile'))
        # Native assets and the application bundle must remain syntactically valid.
        if js_runtime.runtime():
            script='new Function('+json.dumps(bundle)+');'
            with tempfile.TemporaryDirectory() as directory:
                result=js_runtime.run(script,Path(directory))
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
