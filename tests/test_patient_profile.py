"""Profile persistence and admission tests; also runnable with stdlib unittest."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent import config, device, profile, projection
from agent.demo.authoring import claim, confirm, reject, ingested
from agent.events import envelope
from agent.errors import EventValidationError
from agent.server.routes.profile import router
from agent.server.state import RecordState
from agent.vault import Vault, scaffold


class PatientProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        env = patch.dict(os.environ, {device.ENV_CONFIG_HOME: str(root / 'machine')})
        env.start()
        self.addCleanup(env.stop)
        scaffold(root / 'record')
        (root / 'record' / config.CONFIG_FILENAME).write_text('sync_profile = "local"\nport = 7777\nlocale = "en"\n')
        self.vault = Vault.open(root / 'record', identity=device.issue(label='profile-test'))
        self.state = RecordState(self.vault, clock=lambda: datetime(2026,10,18,tzinfo=timezone.utc))
        app = FastAPI()
        app.state.record = self.state
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def save(self, values, revision=None):
        return self.client.put('/api/profile', json={'values':values,'revision':revision})

    def test_read_is_empty_without_writing_and_age_uses_birthday(self):
        response = self.client.get('/api/profile')
        self.assertEqual(response.status_code,200)
        self.assertIsNone(response.json()['revision'])
        self.assertIsNone(response.json()['age'])
        self.assertEqual(len(self.vault.read().events),0)
        data = self.save({'date_of_birth':'1988-10-19'}).json()
        self.assertEqual(data['age'],37)

    def test_main_app_serves_profile_and_all_assets(self):
        from agent.server import create_app
        from agent.runtime import supervisor
        with patch.dict(os.environ, {'HEALTH_AGENT_DATA_HOME':str(Path(self.temp.name)/'reader'), 'PYTEST_CURRENT_TEST':'profile_smoke'}):
            previous = supervisor.install(None)
            try:
                app = create_app(self.vault, worker=False)
                with TestClient(app,base_url='http://127.0.0.1:7777') as client:
                    for path in ('/api/profile','/','/profile','/ask','/patient-profile.js','/patient-profile.css','/appearance.js'):
                        response = client.get(path)
                        self.assertEqual(response.status_code,200,(path,response.text[:150]))
                    self.assertIn('no-store',client.get('/api/profile').headers['cache-control'])
                    self.assertIn('/patient-profile.js',client.get('/').text)
            finally:
                running = supervisor.install(previous)
                if running:
                    running.shutdown()

    def test_profile_survives_cache_removal_and_replay_without_changing_clinical_wiki(self):
        before = projection.project(self.vault.read().events, '2026-10-18T00:00:00Z')
        response = self.save({'full_name':'Alex Example','preferred_name':'Al','medicare_number':'0123 45678 9','allergy_notes':'Patient reports rash with adhesive.'})
        self.assertEqual(response.status_code,200,response.text)
        revision = response.json()['revision']
        response = self.save({'mobile':'+61 400 000 000'},revision)
        self.assertEqual(response.status_code,200,response.text)
        self.state.invalidate()
        fresh = self.client.get('/api/profile').json()
        self.assertEqual(fresh['values']['full_name'],'Alex Example')
        self.assertEqual(fresh['values']['medicare_number'],'0123 45678 9')
        self.assertEqual(fresh['values']['mobile'],'+61 400 000 000')
        after = projection.project(self.vault.read().events,'2026-10-18T00:00:00Z')
        self.assertEqual(before.files,after.files)
        self.assertEqual(before.rows,after.rows)
        self.assertEqual(fresh['clinical']['allergies'],[])
        self.assertEqual(len([e for e in self.vault.read().events if e.type==profile.UPDATED]),2)

    def test_stale_edit_cannot_overwrite_another_windows_changes(self):
        first = self.save({'full_name':'First'}).json()
        stale = self.save({'full_name':'Stale'})
        self.assertEqual(stale.status_code,409)
        self.assertEqual(self.client.get('/api/profile').json()['values']['full_name'],'First')
        unchanged = self.save({'full_name':'First'},first['revision']).json()
        self.assertEqual(unchanged['revision'],first['revision'])
        cleared = self.save({'full_name':''},first['revision']).json()
        self.assertEqual(cleared['values']['full_name'],'')

    def test_invalid_input_writes_no_events(self):
        for values in ({'full_name':123},{'date_of_birth':'2030-01-01'}, {'date_of_birth':'2026-02-30'}, {'medicare_expiry':'2026-13'}, {'interpreter':'Maybe'}, {'unknown':'x'}, {'email':'not-an-email'}, {'full_name':'x'*301}):
            with self.subTest(values=values):
                self.assertEqual(self.save(values).status_code,400)
        self.assertEqual(len(self.vault.read().events),0)

    def test_only_admitted_clinical_entries_reach_the_profile(self):
        ident = self.vault.identity.id
        a = claim(ident,'allergy:penicillin','reaction','Rash',ts='2026-10-01T00:00:00Z')
        pending = claim(ident,'allergy:latex','reaction','Secret pending reaction',ts='2026-10-02T00:00:00Z')
        rejected = claim(ident,'allergy:adhesive','reaction','Secret rejected reaction',ts='2026-10-03T00:00:00Z')
        for event in (ingested(ident), a, confirm(ident,a.id,ts='2026-10-01T01:00:00Z'), pending, rejected, reject(ident,rejected.id,ts='2026-10-03T01:00:00Z')):
            self.state.append(event)
        response = self.client.get('/api/profile')
        self.assertEqual(response.status_code,200,response.text)
        allergies = response.json()['clinical']['allergies']
        self.assertEqual(len(allergies),1)
        self.assertEqual(allergies[0]['reaction'],'Rash')
        self.assertNotIn('Secret pending',response.text)
        self.assertNotIn('Secret rejected',response.text)

    def test_event_actor_is_enforced(self):
        event = envelope.new(profile.UPDATED,self.vault.identity.id,actor='agent',payload={'values':{'full_name':'Model overwrite'}})
        with self.assertRaises(EventValidationError):
            self.vault.append(event)
        self.assertEqual(profile.read(self.vault.read().events)['values']['full_name'],'')


if __name__ == '__main__':
    unittest.main()
