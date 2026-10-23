"""Calendar, DOB migration and facility note privacy checks."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from datetime import datetime,timezone
from unittest.mock import patch

from tests import test_jane_demo as base
from agent.demo.jane import seed,PROFILE
from agent.demo_metadata import entries
from agent.demo import progress
from agent import sealed
from agent.events import envelope


class CalendarProgressTests(unittest.TestCase):
    setUp=base.JaneDemoTests.setUp
    get=base.JaneDemoTests.get

    def decrypt(self,password='pass'):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        snapshot=self.app.state.record.snapshot()
        comp=next(c for c in self.get('/api/doctor-notes').json()['compartments'] if c['id']==progress.COMPARTMENT['compartment'])
        key=hashlib.pbkdf2_hmac('sha256',password.encode(),base64.b64decode(comp['kdf']['salt']),comp['kdf']['iterations'],32)
        cipher=AESGCM(key)
        check=cipher.decrypt(base64.b64decode(comp['check']['iv']),base64.b64decode(comp['check']['ciphertext']),comp['id'].encode()).decode()
        self.assertEqual(check,sealed.CHECK_PLAINTEXT)
        return [json.loads(cipher.decrypt(base64.b64decode(note['iv']),base64.b64decode(note['ciphertext']),comp['id'].encode()))
                for note in self.get('/api/doctor-notes').json()['notes'] if note['compartment']==comp['id']]

    def test_calendar_has_real_sources_upcoming_and_november_birthday(self):
        data=self.get('/api/calendar').json()
        self.assertEqual(data['timezone'],'Australia/Sydney')
        events=data['events']
        birthday=next(e for e in events if e['id']=='birthday:2026')
        self.assertEqual(birthday['date'],'2026-11-01')
        self.assertEqual(birthday['description'],'Turns 91.')
        self.assertEqual(self.get('/api/profile').json()['values']['date_of_birth'],'1935-11-01')
        podiatry=next(e for e in events if e['id']=='demo:podiatry-2026-10-28')
        self.assertEqual(podiatry['time'],'10:30')
        self.assertIn('Daniel Brooks',podiatry['professional'])
        self.assertTrue(podiatry['location'])
        cardio=[e for e in events if e.get('professional')=='Dr Matthew Thompson' and e['date']=='2026-09-24']
        self.assertEqual(len(cardio),1)
        self.assertEqual(cardio[0]['time'],'10:30')
        self.assertIn('Harbourside',cardio[0]['location'])
        for event in events:
            if event.get('source_url','').startswith('/artifact/'):
                meta=self.get('/api/artifact/'+event['source_url'].split('/')[-1]+'/meta').json()
                self.assertTrue(meta['present'])
                self.assertTrue(meta['native'])
        self.assertTrue(any(e['kind']=='prescription' and e['date']=='2026-10-31' for e in events))
        self.assertEqual(len(self.get('/api/timeline').json()['rows']),68)

    def test_calendar_additions_retry_validation_and_no_timeline_clutter(self):
        body={'id':'user:my-appt','title':'Follow-up','date':'2026-11-04','time':'10:30',
              'professional':'My clinician','location':'My practice','kind':'appointment'}
        first=self.client.post('/api/calendar',json=body)
        self.assertEqual(first.status_code,201,first.text)
        before=len(self.vault.read().events)
        self.assertEqual(self.client.post('/api/calendar',json=body).status_code,201)
        self.assertEqual(len(self.vault.read().events),before)
        event=next(e for e in self.get('/api/calendar').json()['events'] if e['id']=='user:my-appt')
        self.assertEqual(event['location'],'My practice')
        self.assertFalse(event['fictional'])
        self.assertEqual(seed(self.root).events,0)
        self.assertTrue(any(e['id']=='user:my-appt' for e in self.get('/api/calendar').json()['events']))
        self.assertEqual(len(self.get('/api/timeline').json()['rows']),68)
        for change in ({'date':'2026-02-30'},{'time':'25:00'},{'id':'demo:forbidden'},{'title':''},
                       {'date':'2026-10-04','time':'02:30'}):
            response=self.client.post('/api/calendar',json={**body,**change})
            self.assertEqual(response.status_code,400,response.text)

    def test_encryption_pass_correct_wrong_password_fails_and_professionals_only(self):
        decoded=self.decrypt()
        self.assertEqual(len(decoded),21)
        self.assertGreaterEqual(min(n['written'] for n in decoded),'2024-10-24')
        self.assertEqual(max(n['written'] for n in decoded),'2026-10-21')
        self.assertTrue(any('Morgan Bell' in n['author'] for n in decoded))
        self.assertTrue(any('Olivia Bennett' in n['author'] for n in decoded))
        self.assertTrue(any('Liam Wilson' in n['author'] for n in decoded))
        self.assertTrue(all('Thompson' not in n['author'] for n in decoded))
        self.assertNotIn('Initial consultation',min(decoded,key=lambda n:n['written'])['text'])
        from cryptography.exceptions import InvalidTag
        with self.assertRaises(InvalidTag): self.decrypt('wrong-password')
        snapshot=self.app.state.record.snapshot()
        private='Reviewed Jane during facility GP cover.'
        self.assertNotIn(private,json.dumps([e.to_dict() for e in snapshot.events]))
        for url in ('/api/calendar','/api/timeline','/api/documents','/api/profile','/api/medications','/api/wiki'):
            self.assertNotIn(private,self.get(url).text)
        summary=self.client.post('/api/summary/preview',json={})
        self.assertNotIn(private,summary.text)

    def test_rerun_keeps_existing_ciphertexts_and_user_entries(self):
        before=self.get('/api/doctor-notes').json()
        # A different existing compartment is retained without opening it.
        old=copy.deepcopy(progress.COMPARTMENT)
        old['compartment']='cmp-'+('e'*32)
        self.vault.append(envelope.new(sealed.COMPARTMENT_CREATED,self.vault.identity.id,payload=old))
        payload={'compartment':old['compartment'],'cipher':'AES-256-GCM','iv':progress.NOTES[0]['iv'],'ciphertext':progress.NOTES[0]['ciphertext']}
        self.vault.append(envelope.new(sealed.NOTE_SEALED,self.vault.identity.id,payload=payload))
        count=len(self.vault.read().events)
        self.assertEqual(seed(self.root).events,0)
        self.assertEqual(len(self.vault.read().events),count)
        after=self.get('/api/doctor-notes').json()
        self.assertEqual(len(after['notes']),22)
        for note in before['notes']: self.assertIn(note,after['notes'])
        self.assertEqual(len(self.decrypt()),21)

    def test_later_user_birthday_edit_is_not_overwritten_by_population(self):
        from agent import profile
        self.vault.append(envelope.new(profile.UPDATED,self.vault.identity.id,
            payload={'values':{'date_of_birth':'1935-11-02'}}))
        self.assertEqual(seed(self.root).events,0)
        self.assertEqual(self.get('/api/profile').json()['values']['date_of_birth'],'1935-11-02')
        birthday=next(e for e in self.get('/api/calendar').json()['events'] if e['id']=='birthday:2026')
        self.assertEqual(birthday['date'],'2026-11-02')

    def test_staff_and_native_birthday_consistency(self):
        profile=self.get('/api/profile').json()
        names={p['name'] for p in profile['care_team'] if p['facility']}
        self.assertEqual(names,{'Dr James Smith','Morgan Bell','Dr Olivia Bennett','Dr Liam Wilson'})
        for doc in self.get('/api/documents').json()['documents']:
            native=self.get('/api/artifact/'+doc['id']+'/meta').json()['native']
            self.assertEqual(native['patient']['dob'],'1935-11-01')
        self.assertEqual(seed(self.root).events,0)
        self.assertEqual(len(self.get('/api/calendar').json()['events']),len({e['id'] for e in self.get('/api/calendar').json()['events']}))

    def test_birthday_uses_sydney_day_and_old_document_links_still_open(self):
        with patch.object(self.app.state.record,'now',return_value=datetime(2026,10,31,13,0,tzinfo=timezone.utc)):
            self.assertEqual(self.get('/api/profile').json()['age'],91)
            self.assertEqual(self.get('/api/calendar').json()['today'],'2026-11-01')
        snapshot=self.app.state.record.snapshot()
        for event in entries(snapshot.events,snapshot.artifacts).values():
            for previous in set(event.payload.get('previous_artifacts',[])):
                if previous in snapshot.artifacts:
                    meta=self.get('/api/artifact/'+previous+'/meta').json()
                    self.assertTrue(meta['native'])
                    self.assertEqual(meta['native']['patient']['dob'],'1935-11-01')

    def test_served_progress_screen_is_short_and_expands_note_rows(self):
        import re
        index=self.get('/progress-notes').text
        bundle=re.search(r'src="(/assets/[^\"]+\.js)"',index).group(1)
        code=self.get(bundle).text
        def function(signature):
            start=code.index(signature)
            end=code.index('function ',start+len(signature))
            return code[start:end]
        unlock=function('function gr({data:e,onOpened:t})')
        opened=function('function _r({data:e,opened:t,onSaved:n,onLock:r})')
        jsc=Path('/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc')
        if not jsc.exists(): self.skipTest('JavaScriptCore unavailable')
        prelude="""const b={jsx:(type,props,key)=>({type,props,key}),jsxs:(type,props,key)=>({type,props,key})};
let idx=0;let notesMode=false;
const shown=[{event_id:'one',ts:'2026-10-23',content:{author:'Morgan Bell · Registered nurse',written:'2026-10-19',text:'Private example'}},{event_id:'two',ts:'2026-10-23',content:{author:'Dr James Smith · Facility GP',written:'2026-10-15',text:'Private GP example'}}];
const l={useState:x=>[notesMode&&idx++===0?shown:typeof x==='function'?x():x,()=>{}],useRef:()=>({}),useEffect:()=>{}};
const cr=async(c,p)=>({id:c.id});const vr=()=> '2026-10-23';const A=x=>x;const ue=x=>x;const j='empty';
const assert=(v,m)=>{if(!v)throw Error(m);};
"""
        checks="""
const locked=gr({data:{notes:[],compartments:[]},onOpened:()=>{}});
const text=JSON.stringify(locked);assert(text.includes('Password protected. Please enter your password.'),'short alert');assert(!text.includes('Anyone who can open'),'no long intro');
notesMode=true;idx=0;const opened=_r({data:{notes:[]},opened:{compartment:{id:'x'}},onSaved:()=>{},onLock:()=>{}});
const output=JSON.stringify(opened);assert((output.match(/\"type\":\"details\"/g)||[]).length===3,'add form and both notes collapse');assert(output.includes('Morgan Bell')&&output.includes('Dr James Smith'),'facility authors');(async()=>{let result;const form=gr({data:{notes:[],compartments:[{id:'first'},{id:'second'}]},onOpened:x=>{result=x;}});await form.props.onSubmit({preventDefault(){}});assert(Object.keys(result.keys).length===2,'same password opens all matching compartments');print('Progress UI rendering passed');})().catch(e=>{print(e.stack);throw e;});
"""
        path=self.temp/'progress-test.js';path.write_text(prelude+unlock+opened+checks)
        result=subprocess.run([str(jsc),str(path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_calendar_ui_months_leap_year_events_and_served_routes(self):
        for path in ('/calendar','/progress-notes','/doctor-notes','/calendar.js','/calendar.css'):
            self.get(path)
        jsc=Path('/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc')
        if not jsc.exists(): self.skipTest('JavaScript runtime unavailable')
        source=(base.ROOT/'frontend/public/calendar.js').read_text()
        data=self.get('/api/calendar').json()
        script='''let Widget;globalThis.HTMLElement=class{};globalThis.customElements={define(n,c){Widget=c;}};
const assert=(v,m)=>{if(!v)throw Error(m);};\n'''+source+'\nconst data='+json.dumps(data)+''';
const view=new Widget();view.data=data;view.month='2026-11';view.render();assert(view.innerHTML.includes('November 2026'),'month');
view.selected='2026-11-01';view.render();assert(view.innerHTML.includes('birthday'),'birthday');
view.month='2024-02';view.selected=null;view.render();assert(view.innerHTML.includes('2024-02-29'),'leap day');
view.move(1);assert(view.month==='2024-03','next');view.move(-1);assert(view.month==='2024-02','previous');
view.month='2026-12';view.move(1);assert(view.month==='2027-01','year boundary');
assert(!view.row({id:'x',date:'2026-11-01',title:'<script>bad()</script>',kind:'event'}).includes('<script>'),'escaped text');print('Calendar UI passed');'''
        path=self.temp/'calendar-test.js';path.write_text(script)
        response=subprocess.run([str(jsc),str(path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(response.returncode,0,response.stdout+response.stderr)
