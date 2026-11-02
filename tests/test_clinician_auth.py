"""Authentication boundaries and account-specific source-based review windows."""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from agent.clinician_auth import AuthStore, COOKIE, IDLE_SECONDS, MAX_SECONDS, provision, store_path
from tests import test_jane_demo as base


class ClinicianTests(unittest.TestCase):
    def setUp(self):
        base.JaneDemoTests.setUp(self)
        self.passwords = provision(self.root)
        self.app.state.clinician_auth = AuthStore(self.root)
        self.app.state.record._clock = lambda: datetime(2026,11,2,0,tzinfo=timezone.utc)

    def login(self, username='james.smith'):
        response = self.client.post('/api/auth/clinician/login', json={'username':username,'password':self.passwords[username]})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_signed_out_boundaries_and_patient_flow(self):
        for path in ['/clinician','/clinician/record','/clinician/documents','/clinician/artifact/test','/progress-notes','/doctor-notes']:
            r=self.client.get(path,follow_redirects=False)
            self.assertEqual(r.status_code,303,path)
            self.assertEqual(r.headers['location'],'/clinician/sign-in')
        for path in ['/api/clinician/dashboard','/api/clinician/profile','/api/clinician/wiki','/api/clinician/timeline','/api/clinician/artifact/test/meta','/api/doctor-notes']:
            r=self.client.get(path)
            self.assertEqual(r.status_code,401,path)
            self.assertEqual(r.headers['cache-control'],'no-store')
        for path in ['/overview','/care-team','/api/profile','/api/calendar','/api/documents','/clinician/sign-in']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        self.assertEqual(self.client.post('/api/doctor-notes',json={}).status_code,401)

    def test_login_hashes_bad_credentials_and_cookie(self):
        saved=store_path(self.root).read_text()
        for password in self.passwords.values(): self.assertNotIn(password,saved)
        for username in ['james.smith','unknown']:
            r=self.client.post('/api/auth/clinician/login',json={'username':username,'password':'incorrect'})
            self.assertEqual(r.status_code,401)
            self.assertEqual(r.json()['detail'],'Incorrect username or password.')
        identity=self.login()
        self.assertEqual(identity['clinician']['name'],'Dr James Smith')
        session=self.client.get('/api/auth/clinician/session')
        self.assertEqual(session.json()['clinician'],identity['clinician'])
        self.assertNotIn('password',json.dumps(session.json()))
        cookie=self.client.cookies.get(COOKIE)
        self.assertGreaterEqual(len(cookie),40)
        self.assertNotIn(cookie,json.dumps(self.app.state.clinician_auth.sessions,default=str))
        self.assertEqual(self.client.get('/api/clinician/profile').status_code,200)

    def test_account_specific_reviews_sources_and_no_writes(self):
        before=len(self.vault.read().events)
        identity=self.login()
        old_cookie=self.client.cookies.get(COOKIE)
        james=self.client.get('/api/clinician/dashboard').json()
        self.assertEqual(james['last_consultation']['date'],'2026-10-21')
        self.assertEqual(james['comparison_date'],'2026-10-21')
        self.assertEqual(james['upcoming']['date'],'2026-11-03')
        self.assertEqual(james['upcoming']['professional'],'Dr James Smith')
        self.assertEqual(james['groups']['medications'],[])
        self.assertTrue(any(r['date']=='2026-10-22' for r in james['groups']['reported']))
        self.login('olivia.bennett')
        olivia=self.client.get('/api/clinician/dashboard').json()
        self.assertEqual(olivia['clinician']['name'],'Dr Olivia Bennett')
        self.assertEqual(olivia['comparison_date'],'2025-03-26')
        self.assertGreater(len(olivia['groups']['documents']),len(james['groups']['documents']))
        pathology=next(row for row in olivia['groups']['documents'] if row['author']=='Dr Sarah Chen')
        self.assertEqual(pathology['role'],'Pathology')
        self.assertIsNone(self.app.state.clinician_auth.resolve(old_cookie))
        for rows in olivia['groups'].values():
            for row in rows:
                self.assertTrue(row['headline'] and row['summary'] and row['author'] and row['role'])
                self.assertIn('status',row)
                self.assertEqual(self.client.get(row['source_url']).status_code,200,row['source_url'])
        self.assertEqual(len(self.vault.read().events),before)
        self.assertNotEqual(identity['csrf'],self.client.get('/api/auth/clinician/session').json()['csrf'])

    def test_logout_csrf_and_revocation(self):
        session=self.login()
        old_cookie=self.client.cookies.get(COOKIE)
        self.assertEqual(self.client.post('/api/auth/clinician/logout',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/doctor-notes',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/doctor-notes',json={},headers={'X-Clinician-CSRF':session['csrf'], 'Origin':'http://127.0.0.1:9999'}).status_code,403)
        r=self.client.post('/api/auth/clinician/logout',json={},headers={'X-Clinician-CSRF':session['csrf']})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/auth/clinician/session').status_code,401)
        self.assertEqual(self.client.get('/api/clinician/dashboard').status_code,401)
        self.client.cookies.set(COOKIE,old_cookie)
        self.assertEqual(self.client.get('/api/clinician/dashboard').status_code,401)

    def test_origin_rate_limit_expiry_and_notes_stay_sealed(self):
        self.assertEqual(self.client.post('/api/auth/clinician/login',json={'username':'james.smith','password':self.passwords['james.smith']},headers={'Origin':'http://localhost:9000'}).status_code,403)
        session=self.login()
        notes=self.client.get('/api/doctor-notes').json()
        self.assertTrue(notes['compartments'])
        self.assertNotIn('Private',json.dumps(notes))
        for note in notes['notes']:
            self.assertIn('ciphertext',note)
            self.assertNotIn('text',note)
        store=self.app.state.clinician_auth
        now=store.clock()
        store.clock=lambda:now+IDLE_SECONDS+1
        self.assertEqual(self.client.get('/api/clinician/dashboard').status_code,401)
        store.clock=lambda:now
        for _ in range(5):
            self.assertEqual(self.client.post('/api/auth/clinician/login',json={'username':'unknown','password':'wrong'}).status_code,401)
        self.assertEqual(self.client.post('/api/auth/clinician/login',json={'username':'james.smith','password':self.passwords['james.smith']}).status_code,429)

    def test_first_review_requires_date_and_patient_assignment(self):
        self.login('olivia.bennett')
        state=self.app.state.record
        snapshot=state.snapshot()
        filtered=tuple(e for e in snapshot.events if not (e.payload.get('calendar_entry',{}).get('professional')=='Dr Olivia Bennett'))
        with patch.object(state,'snapshot',return_value=replace(snapshot,events=filtered)):
            first=self.client.get('/api/clinician/dashboard').json()
            self.assertTrue(first['first_review'])
            self.assertIsNone(first['comparison_date'])
            self.assertTrue(all(not rows for rows in first['groups'].values()))
            dated=self.client.get('/api/clinician/dashboard?comparison_date=2026-10-01')
            self.assertEqual(dated.status_code,200)
            self.assertEqual(dated.json()['comparison_date'],'2026-10-01')
        for invalid in ['tomorrow','2027-01-01','2026-02-30']:
            self.assertEqual(self.client.get('/api/clinician/dashboard',params={'comparison_date':invalid}).status_code,400)
        self.app.state.clinician_auth.accounts['olivia.bennett']['patients']=[]
        self.assertEqual(self.client.get('/api/clinician/dashboard').status_code,401)


if __name__=='__main__': unittest.main()
