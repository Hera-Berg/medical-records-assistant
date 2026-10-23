"""Public native demo documents must be searchable without extraction or a model."""
import copy
import unittest
from unittest.mock import patch
from tests import test_jane_demo as base
from agent.query.classify import classify
from agent.query.vocab import Vocabulary
from agent.query.retrieve import retrieve
from agent.query import Record
from agent.events import envelope

class NativeRecordSearchTests(unittest.TestCase):
    setUp = base.JaneDemoTests.setUp
    get = base.JaneDemoTests.get

    def search(self,question):
        s=self.app.state.record.snapshot()
        return retrieve(classify(question,Vocabulary.of(s.projection.entities),s.as_of.date()),s.projection,s.citer)

    def test_supported_questions_find_actual_contents_and_working_sources(self):
        cases={
            'What allergies do I have?':('penicillin','patient-reported'),
            'When is my birthday?':('1935-11-01','dob'),
            'What did the cardiologist say?':('atrial fibrillation','Thompson'),
            'What was my blood test result?':('Haemoglobin','132'),
            'What did Emily notice?':('Emily','frame'),
            'What are my care preferences?':('plain-language','written'),
        }
        for question,expected in cases.items():
            with self.subTest(question=question):
                result=self.search(question)
                self.assertTrue(result.passages)
                text=' '.join(p.text for p in result.passages)
                for word in expected:self.assertIn(word.lower(),text.lower())
                for passage in result.passages:
                    if passage.predicate=='~source':
                        self.assertTrue(self.get('/api/artifact/'+passage.key+'/meta').json()['native'])
        result=self.search('What did the cardiologist say?')
        self.assertIn('Cardiology',result.passages[0].title)

    def test_unrelated_and_empty_records_stay_empty(self):
        self.assertTrue(self.search('What is my cobalt level?').is_empty)
        record=Record.of([])
        q=classify('What allergies do I have?',Vocabulary.of(record.projection.entities),record.as_of.date())
        self.assertTrue(retrieve(q,record.projection,record.citer).is_empty)

    def test_unreleased_and_protected_sources_do_not_enter_search(self):
        from agent.demo_metadata import entries
        s=self.app.state.record.snapshot()
        event=next(iter(entries(s.events,s.artifacts).values()))
        payload=copy.deepcopy(event.payload)
        payload.update(patient_visible=False,demo_item_id='private-test',title='privatequartz',text='privatequartz')
        payload['native_document']['sections']=[{'heading':'Private','text':'privatequartz'}]
        self.vault.append(envelope.new('note.recorded',self.vault.identity.id,payload=payload))
        self.assertTrue(self.search('privatequartz').is_empty)
        public=' '.join(p.text for p in self.search('What did the cardiologist say?').passages)
        self.assertNotIn('privatequartz',public)

    def test_question_reaches_model_with_native_allergy_source_and_returns_citation(self):
        from agent.extract import session
        from agent.llm.client import Completion
        import json,re
        class Model:
            def complete(inner,messages,**kwargs):
                text='\n'.join(m['content'] for m in messages)
                self.assertIn('penicillin',text.lower())
                key=re.search(r'^\[([^\]]+)\].*penicillin',text,re.M|re.I).group(1)
                return Completion(content=json.dumps({'sentences':[{'text':'You reported an itchy rash after penicillin; this is not clinician-verified.','source':key}]}),model='test',raw={},latency_s=0)
            def close(inner):pass
        before=len(self.vault.read().events)
        with patch.object(session,'reads_here',return_value=False),patch.object(session,'open_client',return_value=Model()):
            result=self.client.post('/api/ask',json={'question':'What allergies do I have?'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['state'],'answered',result.text)
        self.assertTrue(result.json()['sentences'][0]['citation'])
        self.assertEqual(len(self.vault.read().events),before)
