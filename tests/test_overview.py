"""Exercise the overview's rendering, read failures and cancellation without a model."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
JSC = '/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc'


class OverviewTests(unittest.TestCase):
    def test_live_data_empty_states_escaping_and_partial_failures(self):
        runtime = shutil.which('node') or (JSC if Path(JSC).is_file() else None)
        if not runtime:
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
const profile={values:{preferred_name:'Alex',full_name:'Alex Example',gp_name:'Dr Example',gp_practice:'Example practice'},clinical:{medications:[{id:'med:test',name:'Test medication',dose:'Recorded dose',stale:true},{id:'med:conflict',name:'Conflicted medicine',dose:'NEVER DISPLAY THIS DOSE',conflicted:true}],allergies:[{id:'allergy:test'}]},review_count:2};
const timeline={rows:[{subject_id:'med:test',marker:'prescriber-issued',date:{render:'around June 2026'},date_label:'document dated',text:'Recorded fact',citation:{resolved:true,artifact:'abc123'}}]};
const view=new Overview();view.profile=profile;view.timeline=timeline;view.render();
assert(view.innerHTML.includes('Alex'),'personal greeting');
assert(view.innerHTML.includes('around June 2026 · document dated'),'date precision and provenance preserved');
assert(view.innerHTML.includes('Recorded fact'),'real timeline content');
assert(view.innerHTML.includes('Needs confirming'),'stale medication visible');
assert(view.innerHTML.includes('Conflicting entries') && !view.innerHTML.includes('NEVER DISPLAY THIS DOSE'),'conflicted dose withheld');
assert(view.innerHTML.includes('Dr Example'),'actual care team');
for (const path of ['/profile','/summary','/timeline','/record','/add','/settings','/review']) assert(view.innerHTML.includes('href="'+path+'"'),'working destination '+path);
assert(!view.innerHTML.includes('24 October'),'no invented appointment');
assert(!view.innerHTML.includes('Manage access'),'no unsupported sharing action');
profile.values.preferred_name='<img src=x onerror=alert(1)>';
timeline.rows[0].text='<script>unsafe()</script>';
view.render();assert(!view.innerHTML.includes('<img') && !view.innerHTML.includes('<script>'),'patient content is text');
view.profile={values:{},clinical:{medications:[],allergies:[]},review_count:0};view.timeline={rows:[]};view.render();
assert(view.innerHTML.includes('Your story starts here'),'empty timeline');
assert(view.innerHTML.includes('Allergy status not established'),'empty does not mean no allergies');
assert(!view.innerHTML.includes('Good morning, Alex'),'no invented identity');
assert(view.innerHTML.includes('href="/files"'),'documents shortcut');
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
        completed = subprocess.run([runtime,'-e',prelude + source + checks],capture_output=True,text=True,timeout=15)
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
        bundle=next((static/'assets').glob('*.js')).read_text()
        for label in ('Overview','Patient profile','Ask your record','Add something','Waiting for you','Timeline','Medications and more','For an appointment',"Doctor's notes",'Files','Settings'):
            self.assertIn(label,source)
            self.assertIn(label,bundle)
        self.assertIn('first === "overview"',(ROOT/'frontend/src/App.tsx').read_text())
        self.assertIn('x===`overview`?(0,b.jsx)(`health-overview`',bundle)
        self.assertIn('x===`profile`?(0,b.jsx)(`patient-profile`',bundle)
        # Native assets and the application bundle must remain syntactically valid.
        runtime = shutil.which('node') or (JSC if Path(JSC).is_file() else None)
        if runtime:
            script='new Function('+json.dumps(bundle)+');'
            result=subprocess.run([runtime,'-e',script],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
