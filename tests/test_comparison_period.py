"""Edge dates are isolated fixtures; Jane's historical sources are never edited."""
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from tests import test_clinician_auth as auth_tests
from agent import ingest
from agent.events import envelope
from agent.demo_metadata import SCENARIO
from agent.server.comparison import resolve, appointment_time


class ComparisonTests(unittest.TestCase):
    setUp=auth_tests.ClinicianTests.setUp
    login=auth_tests.ClinicianTests.login

    def source(self, identifier, event_date, *, timestamp=None, uploaded='2026-10-24T01:00:00Z', authored='2026-09-02', received=None):
        with patch('agent.ingest.envelope.format_ts',return_value=uploaded):
            result=ingest.ingest_bytes(self.vault,('Isolated source '+identifier+' dated '+event_date).encode(),ingest.CaptureContext(source='import',original_filename=identifier+'.txt'))
        native={'date':event_date,'authored_date':authored,'sections':[{'heading':'Source','text':'Isolated source '+identifier}]}
        if received:native['received_at']=received
        payload={'demo_scenario':SCENARIO,'demo_item_id':identifier,'artifact':result.short,
                 'fictional':True,'patient_visible':True,'title':identifier,'text':'Source-supported fixture observation.',
                 'author':'Jane Doe','source_type':'Patient observation','event_type':'observation',
                 'event_date':event_date,'native_document':native,'status':'Isolated fixture'}
        if timestamp:payload['event_timestamp']=timestamp
        self.app.state.record.append(envelope.new('note.recorded',self.vault.identity.id,ts=uploaded,payload=payload))
        return result.short

    def test_own_defaults_custom_reset_and_no_consultation_writes(self):
        self.login()
        before=len(self.vault.read().events)
        original=self.client.get('/api/clinician/dashboard').json()
        self.assertEqual(original['comparison_period']['start_timestamp'],'2026-10-21T09:30:00+11:00')
        custom=self.client.get('/api/clinician/dashboard?comparison_date=2026-10-01').json()
        self.assertEqual(custom['comparison_period']['mode'],'custom')
        self.assertEqual(custom['comparison_period']['start_timestamp'],'2026-10-01T00:00:00+10:00')
        reset=self.client.get('/api/clinician/dashboard').json()
        self.assertEqual(reset['comparison_period'],original['comparison_period'])
        self.assertEqual(len(self.vault.read().events),before)
        self.login('olivia.bennett')
        olivia=self.client.get('/api/clinician/dashboard').json()
        self.assertEqual(olivia['comparison_period']['start_date'],'2025-03-26')
        self.assertNotEqual(olivia['comparison_period']['start_timestamp'],original['comparison_period']['start_timestamp'])
        for value in ('2026-11-03','bad','2026-02-30','2026-10-01T00:00:00Z'):
            self.assertEqual(self.client.get('/api/clinician/dashboard',params={'comparison_date':value}).status_code,400)

    def test_old_document_received_later_has_independent_dates(self):
        short=self.source('old-new-receipt','2026-09-01',authored='2026-09-02',received='2026-10-24T04:00:00Z')
        self.source('upload-only','2026-08-01',authored=None)
        self.login()
        data=self.client.get('/api/clinician/dashboard').json()
        row=next(r for r in data['groups']['newly_received'] if r['id']=='old-new-receipt')
        self.assertEqual(row['event_date'],'2026-09-01')
        self.assertEqual(row['authored_date'],'2026-09-02')
        self.assertEqual(row['received_date'],'2026-10-24')
        self.assertEqual(row['uploaded_at'],'2026-10-24T01:00:00Z')
        self.assertFalse(any(r['id']=='old-new-receipt' for r in data['groups']['reported']))
        absent=next(r for r in data['groups']['newly_received'] if r['id']=='upload-only')
        self.assertIsNone(absent['received_date']);self.assertIsNone(absent['authored_date'])
        self.assertIn('not recorded',data['receipt_limitation'])
        self.assertEqual(self.client.get('/api/clinician/artifact/'+short+'/meta').status_code,200)

    def test_same_day_timestamp_and_unknown_ordering(self):
        self.source('before-consult','2026-10-21',timestamp='2026-10-21T08:00:00+11:00')
        self.source('after-consult','2026-10-21',timestamp='2026-10-21T10:00:00+11:00')
        self.source('date-only','2026-10-21')
        self.login()
        rows=self.client.get('/api/clinician/dashboard').json()['groups']['reported']
        by_id={r['id']:r for r in rows}
        self.assertNotIn('before-consult',by_id)
        self.assertIn('after-consult',by_id)
        self.assertIn('ordering unknown',by_id['date-only']['ordering'])
        account={'name':'Dr James Smith'}
        p=resolve([{'date':'2026-10-21','kind':'appointment','status':'Attended','professional':'Dr James Smith'}],account,datetime(2026,11,2,tzinfo=timezone.utc))
        self.assertEqual(p.data['precision'],'date')
        self.assertIn('time not recorded',p.data['boundary_note'])
        self.assertEqual(p.position('2026-10-21T10:00:00+11:00'),'same-day-unknown')
        complete=resolve([{'date':'2026-10-21','completed_at':'2026-10-21T14:30:00Z','kind':'appointment','status':'Completed','professional':'Dr James Smith'}],account,datetime(2026,11,2,tzinfo=timezone.utc))
        self.assertEqual(complete.data['start_date'],'2026-10-22')
        self.assertEqual(complete.position('2026-10-22'),'same-day-unknown')
        self.assertIsNone(p.position('2026-10-20'))
        self.assertIsNone(appointment_time({'date':'2026-10-04','time':'02:30'}))
        self.assertIsNone(appointment_time({'date':'2026-04-05','time':'02:30'}))
        self.assertIsNotNone(appointment_time({'date':'2026-04-05','completed_at':'2026-04-05T02:30:00+10:00'}))

    def test_sydney_midnight_unknown_observation_and_unresolved_older_request(self):
        for stamp,text in [('2026-10-20T12:59:00Z','Before Sydney day'),('2026-10-20T13:01:00Z','After Sydney midnight')]:
            self.app.state.record.append(envelope.new('note.recorded',self.vault.identity.id,ts=stamp,payload={'source':'typed','text':text}))
        self.login()
        data=self.client.get('/api/clinician/dashboard?comparison_date=2026-10-21').json()
        rows=data['groups']['reported']
        self.assertFalse(any(r['summary']=='Before Sydney day' for r in rows))
        row=next(r for r in rows if r['summary']=='After Sydney midnight')
        self.assertEqual(row['date'],'2026-10-21');self.assertIsNone(row['event_date'])
        self.assertIn('Observation date not supplied',row['date_basis'])
        late=self.client.get('/api/clinician/dashboard?comparison_date=2026-10-30').json()
        request=next(r for r in late['groups']['follow_up'] if r['id']=='jane-history-68')
        self.assertEqual(request['date'],'2026-10-22');self.assertTrue(request['outside_period'])
        self.assertFalse(any(r['date']>'2026-11-02' for key,rows in late['groups'].items() if key!='follow_up' for r in rows))

    def test_first_review_summary_needs_a_chosen_date(self):
        from dataclasses import replace
        session=self.login('olivia.bennett')
        state=self.app.state.record
        snapshot=state.snapshot()
        events=tuple(e for e in snapshot.events if e.payload.get('calendar_entry',{}).get('professional')!='Dr Olivia Bennett')
        with patch.object(state,'snapshot',return_value=replace(snapshot,events=events)):
            headers={'X-Clinician-CSRF':session['csrf']}
            denied=self.client.post('/api/clinician/summary/preview',json={},headers=headers)
            self.assertEqual(denied.status_code,409)
            self.assertIn('First review',denied.json()['detail'])
            allowed=self.client.post('/api/clinician/summary/preview',json={'since':'2026-10-01'},headers=headers)
            self.assertEqual(allowed.status_code,200,allowed.text)
            self.assertEqual(allowed.json()['comparison_period']['mode'],'custom')

    def test_clinician_summary_uses_same_period_and_patient_basis_is_separate(self):
        # A prepared document's snapshot must be later than its fixture ingest
        # events, irrespective of the developer machine's real clock.
        state=self.app.state.record
        clock=max([state.now(),*(e.instant for e in state.snapshot().events if e.instant)])+timedelta(seconds=1)
        state._clock=lambda:clock
        state.invalidate()
        session=self.login()
        headers={'X-Clinician-CSRF':session['csrf']}
        dashboard=self.client.get('/api/clinician/dashboard').json()
        preview=self.client.post('/api/clinician/summary/preview',json={},headers=headers)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['comparison_period'],dashboard['comparison_period'])
        self.assertIn('own last completed consultation',preview.json()['comparison_basis'])
        for section in preview.json()['sections']:
            for line in section['lines']:
                self.assertTrue(all(s['citation']['resolved'] for s in line['sources']))
                for source in line['sources']:
                    if source.get('url'):
                        self.assertEqual(self.client.get(source['url']).status_code,200)
        custom=self.client.post('/api/clinician/summary/preview',json={'since':'2026-10-01'},headers=headers)
        self.assertEqual(custom.json()['comparison_period']['mode'],'custom')
        prepared=self.client.post('/api/clinician/summary',json={'since':'2026-10-01'},headers=headers)
        self.assertEqual(prepared.status_code,200,prepared.text)
        identifier=prepared.json()['id']
        restored=self.client.get('/api/clinician/summary/'+identifier)
        self.assertEqual(restored.status_code,200)
        self.assertEqual(restored.json()['comparison_period'],prepared.json()['comparison_period'])
        self.assertEqual(restored.json()['markdown'],prepared.json()['markdown'])
        self.assertEqual(self.client.get('/clinician/print/'+identifier).status_code,200)
        self.assertEqual(self.client.get('/api/clinician/dashboard').json()['comparison_date'],'2026-10-21')
        patient=self.client.post('/api/summary/preview',json={}).json()
        self.assertIn('Patient/carer summary basis',patient['comparison_basis'])
        self.assertNotEqual(patient['since'],'2026-10-01')
        self.assertFalse(any(r['id']==identifier for r in self.client.get('/api/summary').json()['summaries']))
        self.login('olivia.bennett')
        self.assertEqual(self.client.get('/api/clinician/summary/'+identifier).status_code,403)
        self.assertEqual(self.client.get('/api/summary/'+identifier).status_code,403)
        self.assertEqual(self.client.get('/print/'+identifier).status_code,403)
        olivia=self.client.post('/api/clinician/summary/preview',json={},headers={'X-Clinician-CSRF':self.client.get('/api/auth/clinician/session').json()['csrf']}).json()
        self.assertEqual(olivia['comparison_period']['start_date'],'2025-03-26')


if __name__=='__main__': unittest.main()
