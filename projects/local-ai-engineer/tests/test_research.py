import json
import unittest
from unittest.mock import patch

from engine.controller import Controller
from engine.expert import ExpertUnavailable
from engine.research import GeminiAdviser, public_query, web_search
import test_acceptance as baseline


class ResearchTests(unittest.TestCase):
    setUp = baseline.Acceptance.setUp
    tearDown = baseline.Acceptance.tearDown

    @patch('engine.research.request')
    def test_search_parser_and_query_gate(self, request):
        request.return_value = ('<a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F">Python</a>'
                                '<td class="result-snippet">Official docs</td>'
                                '<a class="result-link" href="javascript:alert(1)">Ignore</a>')
        self.assertEqual(web_search('python docs'),[{'url':'https://docs.python.org/','title':'Python','snippet':'Official docs'}])
        with self.assertRaises(ValueError):
            web_search('read C:\\Users\\private.txt')
        with self.assertRaises(ValueError):
            public_query('token=private')
        request.assert_called_once()

    @patch.dict('os.environ',{'GEMINI_API_KEY':'gemini-test-key'})
    @patch('engine.research.request')
    def test_gemini_advice_and_auth_header(self, request):
        request.return_value = json.dumps({'candidates':[{'content':{'parts':[
            {'text':json.dumps({'diagnosis':'Review the API','steps':['Read the docs']})}]}}]})
        advice = GeminiAdviser().advise('id','Consult on an API',1,lambda:False)
        self.assertEqual(advice['provider'],'gemini')
        host, path, _, body, headers = request.call_args.args
        self.assertEqual(host,'generativelanguage.googleapis.com')
        self.assertTrue(path.endswith(':generateContent'))
        self.assertNotIn(b'gemini-test-key',body)
        self.assertEqual(headers['x-goog-api-key'],'gemini-test-key')
        self.assertNotIn('tools',json.loads(body))

    @patch.dict('os.environ',{},clear=True)
    @patch('engine.research.request')
    def test_missing_gemini_key_never_contacts_provider(self, request):
        with self.assertRaisesRegex(ExpertUnavailable,'missing'):
            GeminiAdviser().advise('id','query',1,lambda:False)
        request.assert_not_called()

    @patch('engine.controller.web_search',return_value=[{'title':'Python','url':'https://docs.python.org/','snippet':'Reference'}])
    def test_search_results_enter_next_local_request(self, search):
        prompts = []
        class Model:
            def chat(self,prompt,**kwargs):
                prompts.append(prompt)
                if len(prompts)==1:
                    return {'type':'WEB_SEARCH','query':'python docs'}
                return {'type':'PLAN','steps':[{'path':'code.py','purpose':'Implement task','depends_on':[]}]}
        worker = Controller(self.base,{**self.cfg,'session_web':True,'max_iterations':2},self.store,Model())
        result = worker.run(worker.new('demo','Implement a Python function'))
        search.assert_called_once()
        self.assertIn('https://docs.python.org/',prompts[1])
        self.assertEqual(result['web_calls'],1)

    @patch('engine.controller.web_search')
    def test_disabled_search_cannot_dispatch_model_action(self, search):
        worker = Controller(self.base,{**self.cfg,'max_iterations':1},self.store,
                            baseline.MockModel({'type':'WEB_SEARCH','query':'python'}))
        worker.run(worker.new('demo','Implement a Python function'))
        search.assert_not_called()
