"""Optional OpenAI provider, tested without sending records or credentials online."""
import json
import unittest
from unittest.mock import patch
import httpx
from agent.errors import EndpointNotPrivate, CredentialError, ModelIdentityMismatch
from agent.llm.endpoint import parse
from agent.llm.client import Client
from agent.llm.settings import VlmSettings, AuthSettings
from agent.llm import credentials
from agent.server.endpoint_check import settings_for

URL = 'https://api.openai.com/v1'

class OpenAIProviderTests(unittest.TestCase):
    def test_only_official_https_endpoint_is_cloud(self):
        self.assertTrue(parse(URL).is_openai)
        for url in ('http://api.openai.com/v1', URL+'/other', 'https://api.openai.com.evil.test/v1', 'https://api.openai.com:444/v1'):
            with self.assertRaises(EndpointNotPrivate):
                parse(url).verify(lambda *_: ['8.8.8.8'])
        for url in (URL+'?key=secret', URL+'#fragment', 'https://user:secret@api.openai.com/v1'):
            with self.assertRaises(EndpointNotPrivate): parse(url)

    def test_address_check_repeated_and_rebinding_blocked(self):
        endpoint = parse(URL)
        self.assertEqual(endpoint.verify(lambda *_: ['8.8.8.8']), ('8.8.8.8',))
        with self.assertRaises(EndpointNotPrivate):
            endpoint.verify(lambda *_: ['127.0.0.1'])
        self.assertEqual(parse('http://localhost/v1').verify(lambda *_:['127.0.0.1']), ('127.0.0.1',))

    def test_request_and_actual_model_provenance(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200,json={'model':'example-2026-01-01','choices':[{'message':{'content':'{"word":"hello"}'},'finish_reason':'stop'}]})
        with Client(VlmSettings(endpoint=parse(URL),model='example'), transport=httpx.MockTransport(respond), resolver=lambda *_:['8.8.8.8'],credential=lambda:credentials.Credential('test-key','test')) as client:
            result=client.complete([{'role':'user','content':'Read this'}],schema={'type':'object','properties':{'word':{'type':'string'}}})
        body=json.loads(seen[0].content)
        self.assertEqual(str(seen[0].url), URL+'/chat/completions')
        self.assertEqual(seen[0].headers['authorization'],'Bearer test-key')
        self.assertEqual(body['response_format'],{'type':'json_object'})
        for key in ('max_pixels','chat_template_kwargs','temperature','presence_penalty','max_tokens'): self.assertNotIn(key,body)
        self.assertEqual(body['max_completion_tokens'],4096)
        self.assertIn('JSON',body['messages'][0]['content'])
        self.assertEqual(result.model,'example-2026-01-01')

    def test_pinned_model_cannot_drift(self):
        with Client(VlmSettings(endpoint=parse(URL),model='example-2026-01-01')) as client:
            with self.assertRaises(ModelIdentityMismatch): client._check_identity('example-2026-02-01')

    def test_openai_never_uses_private_credentials(self):
        with patch.dict('os.environ', {'PRIVATE_KEY':'private-secret'},clear=True), patch.object(credentials,'_from_keychain',return_value=None) as keychain, patch.object(credentials,'_from_file',return_value=None):
            with self.assertRaises(CredentialError): credentials.resolve('PRIVATE_KEY',openai=True)
            keychain.assert_called_once_with('openai-api-key')
        with patch.dict('os.environ',{'OPENAI_API_KEY':'cloud-secret','PRIVATE_KEY':'private-secret'},clear=True):
            self.assertEqual(credentials.resolve('PRIVATE_KEY',openai=True).value,'cloud-secret')

    def test_guard_precedes_credentials(self):
        with Client(VlmSettings(endpoint=parse(URL),model='example'),resolver=lambda *_:['127.0.0.1'],credential=lambda:self.fail('key read before guard')) as client:
            with self.assertRaises(EndpointNotPrivate): client.list_models()

    def test_draft_does_not_inherit_private_environment(self):
        old=VlmSettings(endpoint=parse('http://localhost/v1'),model='local',auth=AuthSettings(api_key_env='PRIVATE_KEY'))
        self.assertEqual(settings_for({'base_url':URL},old).auth.api_key_env,'OPENAI_API_KEY')
        cloud=settings_for({'base_url':URL},old)
        self.assertIsNone(settings_for({'base_url':'http://localhost/v1'},cloud).auth.api_key_env)

class OpenAISettingsRouteTests(unittest.TestCase):
    def setUp(self):
        from tests.test_jane_demo import JaneDemoTests
        JaneDemoTests.setUp(self)

    def test_store_key_is_separate_set_only_and_served_settings_work(self):
        with patch.object(credentials,'store',return_value='keychain (health-agent/openai-api-key)') as store:
            response=self.client.post('/api/settings/endpoint/key',json={'key':'dummy-cloud-secret','base_url':URL})
        self.assertEqual(response.status_code,200,response.text)
        store.assert_called_once_with('dummy-cloud-secret',self.vault.root,openai=True)
        self.assertNotIn('dummy-cloud-secret',response.text)
        self.assertNotIn('dummy-cloud-secret',(self.root/'config.toml').read_text())
        settings=self.client.get('/api/settings').json()
        self.assertIn('sent to OpenAI',settings['endpoint']['explanation'])
        page=self.client.get('/settings')
        self.assertEqual(page.status_code,200)
        import re
        asset=re.search(r'src="([^"]+index-[^"]+\.js)"',page.text).group(1)
        js=self.client.get(asset)
        self.assertEqual(js.status_code,200)
        self.assertIn('Use OpenAI API',js.text)
        self.assertIn('g.setEndpointKey(C,i)',js.text)

    def test_store_rejects_embedded_credentials(self):
        with patch.object(credentials,'store') as store:
            response=self.client.post('/api/settings/endpoint/key',json={'key':'dummy','base_url':'https://user:password@api.openai.com/v1'})
        self.assertEqual(response.status_code,400)
        store.assert_not_called()

class OpenAIReasoningRegressionTests(unittest.TestCase):
    def make_client(self, handler, model='gpt-6-luna'):
        return Client(VlmSettings(endpoint=parse(URL),model=model),transport=httpx.MockTransport(handler),resolver=lambda *_:['8.8.8.8'],credential=lambda:credentials.Credential('test-key','test'))

    def test_luna_small_probes_have_visible_output_budget_and_no_reasoning(self):
        seen=[]
        def handler(req):
            body=json.loads(req.content); seen.append(body)
            # Reproduce the old API symptom when reasoning consumed the entire cap.
            good=body.get('reasoning_effort')=='none' and body['max_completion_tokens']>=4096
            content=('TEST WORDS' if 'response_format' not in body else '{"word":"ready"}') if good else ''
            return httpx.Response(200,json={'model':'gpt-6-luna','choices':[{'message':{'content':content},'finish_reason':'stop' if good else 'length'}]})
        with self.make_client(handler) as client:
            self.assertTrue(client.probe_vision('image/png','fixture','TEST WORDS'))
            self.assertEqual(client.probe_grammar().content,'{"word":"ready"}')
        for body in seen:
            self.assertEqual(body['reasoning_effort'],'none')
            self.assertFalse(body['store'])

    def test_incomplete_image_response_is_not_classified_as_image_blind(self):
        from agent.errors import OutputTruncated
        def handler(req):
            return httpx.Response(200,json={'model':'gpt-6-luna','choices':[{'message':{'content':None},'finish_reason':'length'}]})
        with self.make_client(handler) as client:
            with self.assertRaises(OutputTruncated): client.probe_vision('image/png','fixture','TEST')

    def test_snapshot_keeps_luna_reasoning_setting(self):
        def handler(req):
            self.assertEqual(json.loads(req.content)['reasoning_effort'],'none')
            return httpx.Response(200,json={'model':'gpt-6-luna-2026-09-22','choices':[{'message':{'content':'hello'},'finish_reason':'stop'}]})
        with self.make_client(handler,model='gpt-6-luna-2026-09-22') as client:
            self.assertEqual(client.complete([{'role':'user','content':'hello'}]).content,'hello')

class OpenAIAskDeliveryTests(OpenAISettingsRouteTests):
    def test_cited_answer_reaches_ask_response(self):
        import re
        from agent.extract import session
        before=len(self.vault.read().events)
        def handler(req):
            body=json.loads(req.content)
            self.assertEqual(body['reasoning_effort'],'none')
            if body.get('response_format'):
                # Search expansion uses terms; actual answer uses cited sentences.
                schema_text=body['messages'][0]['content']
                if 'health_record' not in schema_text and '"sentences"' not in schema_text:
                    content='{"terms":[]}'
                else:
                    prompt='\n'.join(m['content'] for m in body['messages'] if isinstance(m['content'],str))
                    match=re.search(r'^\[([^\]]+)\]',prompt,re.M)
                    self.assertIsNotNone(match)
                    content=json.dumps({'sentences':[{'text':'The record includes medication information.','source':match.group(1)}]})
            return httpx.Response(200,json={'model':'gpt-6-luna','choices':[{'message':{'content':content},'finish_reason':'stop'}]})
        with patch.object(session,'reads_here',return_value=False),patch.object(session,'open_client',side_effect=lambda *_:OpenAIReasoningRegressionTests().make_client(handler)):
            response=self.client.post('/api/ask',json={'question':'What medications am I taking?'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['state'],'answered',response.text)
        self.assertEqual(len(response.json()['sentences']),1)
        self.assertTrue(response.json()['sentences'][0]['citation'])
        self.assertEqual(len(self.vault.read().events),before)

    def test_connect_passes_real_text_and_image_probes_then_saves(self):
        from agent.server import endpoint_check
        from agent.extract.probe import PROBE_TEXT
        from agent.llm.endpoint import Endpoint
        seen=[]
        def handler(req):
            if req.method=='GET': return httpx.Response(200,json={'data':[{'id':'gpt-6-luna'}]})
            body=json.loads(req.content);seen.append(body)
            content='{"word":"ready"}' if body.get('response_format') else PROBE_TEXT
            return httpx.Response(200,json={'model':'gpt-6-luna','choices':[{'message':{'content':content},'finish_reason':'stop'}]})
        with patch.object(Endpoint,'verify',return_value=('8.8.8.8',)),patch.object(endpoint_check,'open_client',side_effect=lambda *_:OpenAIReasoningRegressionTests().make_client(handler)):
            response=self.client.post('/api/settings/endpoint/connect',json={'base_url':URL,'model':'gpt-6-luna'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['saved'],response.text)
        self.assertTrue(response.json()['check']['ok'])
        self.assertIn(URL,(self.root/'config.toml').read_text())
        self.assertEqual(len(seen),2)
        self.assertTrue(any(isinstance(m.get('content'),list) for m in seen[-1]['messages']))
