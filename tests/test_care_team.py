"""Care membership is explicit; historical authorship alone never adds a person."""
import unittest
from types import SimpleNamespace
from tests import test_jane_demo as base
from agent.care_team import listing
from agent.events import envelope
from agent.demo_metadata import SCENARIO


class CareTeamTests(unittest.TestCase):
    setUp = base.JaneDemoTests.setUp
    get = base.JaneDemoTests.get

    def test_complete_team_and_documented_locations(self):
        before = len(self.vault.read().events)
        data = self.get('/api/profile').json()['care_team']
        people = {m['id']: m for m in data}
        self.assertEqual(set(people), {'jamessmith', 'morganbell', 'oliviabennett', 'liamwilson',
            'matthewthompson', 'ameliapatel', 'sarahgreen', 'oliviareed', 'danielbrooks',
            'harbourclinicalpharmacy', 'emilydoe'})
        self.assertEqual(len(data), 11)
        self.assertEqual(people['sarahgreen']['organisation'], 'Sydney Aged Care')
        self.assertEqual(people['sarahgreen']['location'], 'Sydney Aged Care · therapy room')
        self.assertEqual(people['oliviareed']['organisation'], 'Example Community Audiology')
        self.assertIn('2 Demonstration Road', people['oliviareed']['location'])
        self.assertIn('14 Example Wharf', people['matthewthompson']['location'])
        self.assertEqual(people['danielbrooks']['location'], 'Sydney Aged Care · allied health room')
        self.assertNotIn('location', people['ameliapatel'])
        self.assertEqual(people['harbourclinicalpharmacy']['kind'], 'organisation')
        self.assertEqual(people['emilydoe']['group'], 'family')
        self.assertEqual(len(self.vault.read().events), before, 'Projection must not reseed or write records')

    def test_duplicate_names_do_not_duplicate_members_or_promote_sealed_notes(self):
        event = envelope.new('note.recorded', self.vault.identity.id, payload={
            'demo_scenario':SCENARIO, 'care_team':[
                {'name':'Dr James Smith','role':'Usual GP','organisation':'Sydney Aged Care','facility':True},
                {'name':'dr. JAMES SMITH','role':'Usual GP','organisation':'Sydney Aged Care','facility':True},
                {'name':'Dr Olivia Bennett','role':'Locum GP','organisation':'Sydney Aged Care','facility':True},
                {'name':'Dr Liam Wilson','role':'Locum GP','organisation':'Sydney Aged Care','facility':True}]})
        snapshot = self.app.state.record.snapshot()
        data = listing((*snapshot.events, event), snapshot.artifacts, {})
        self.assertEqual(len([m for m in data if m['id']=='jamessmith']), 1)
        self.assertIn('oliviabennett', {m['id'] for m in data})
        self.assertIn('liamwilson', {m['id'] for m in data})
        self.assertNotIn('samtaylor', {m['id'] for m in data})
        self.assertNotIn('alexlee', {m['id'] for m in data})
        sealed = SimpleNamespace(type='doctor.note.saved', payload={'author':'Private Person', 'care_team':[{'name':'Secret','role':'Doctor'}]})
        self.assertEqual(listing((sealed,), {}, {}), [])

    def test_ordinary_profile_is_not_populated_with_demo_team(self):
        self.assertEqual(listing((), {}, {}), [])
        data = listing((), {}, {'gp_name':'Dr Real Example','gp_practice':'Entered practice', 'pharmacy':'Entered pharmacy'})
        self.assertEqual({m['name'] for m in data}, {'Dr Real Example','Entered pharmacy'})
        self.assertTrue(all('location' not in m for m in data))


if __name__ == '__main__':
    unittest.main()
