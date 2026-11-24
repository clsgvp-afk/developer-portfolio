import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from engine.context import pack
from engine.expert import AnthropicExpert, CloudLedger, ExpertUnavailable, anthropic_messages, consultation, escalation_reason
from engine.controller import Controller
from engine.storage import Store
import test_acceptance as baseline


KEY = 'sk-ant-test-not-a-real-key-0000'


def response():
    return {'usage':{'input_tokens':1000,'output_tokens':100}, 'content':[{'type':'text',
        'text':json.dumps({'diagnosis':'Missing normalization', 'steps':['Apply a focused patch', 'Run tests']})}]}


class HybridTests(unittest.TestCase):
    setUp = baseline.Acceptance.setUp
    tearDown = baseline.Acceptance.tearDown

    def expert(self, config=None, approve=None, budget=None, transport=None):
        self.transport = transport or Mock(return_value=response())
        return AnthropicExpert({'enabled':True, **(config or {})}, CloudLedger(self.store.db),
                               approve or Mock(return_value=True), 'session', budget, self.transport)

    def test_disabled_and_missing_key_never_dispatch(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), patch.dict('os.environ', {}, clear=True):
                expert = self.expert({'enabled':enabled})
                with self.assertRaisesRegex(ExpertUnavailable,'missing' if enabled else 'disabled'):
                    expert.advise('task', 'content', 1)
                self.transport.assert_not_called()
                self.assertEqual(expert.ledger.totals()['calls'],0)

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY':KEY})
    def test_explicit_denial_has_no_request_or_charge(self):
        approve = Mock(return_value=False)
        expert = self.expert(approve=approve)
        with self.assertRaisesRegex(ExpertUnavailable,'declined'):
            expert.advise('task','api_key='+KEY,1)
        self.assertNotIn(KEY,approve.call_args.args[0])
        self.transport.assert_not_called()
        self.assertEqual(expert.ledger.totals()['calls'],0)

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY':KEY})
    def test_budget_and_call_limit_survive_reopen(self):
        for expert in (self.expert({'daily_budget_usd':3}), self.expert(budget=3_000_000)):
            with self.assertRaisesRegex(ExpertUnavailable,'limit'):
                expert.advise('task','small payload',1)
            expert.transport.assert_not_called()
        expert = self.expert({'max_calls_per_task':1})
        advice = expert.advise('task','small payload',1)
        self.assertEqual(advice['cost_usd'],.006)
        fresh = Store(self.base/'data/test.sqlite3')
        try:
            ledger = CloudLedger(fresh.db)
            self.assertEqual(ledger.totals()['micros'],6000)
            expert.ledger = ledger
            with self.assertRaisesRegex(ExpertUnavailable,'limit'):
                expert.advise('task','retry',1)
        finally:
            fresh.db.close()
        self.transport.assert_called_once()

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY':KEY})
    def test_uncertain_request_keeps_reserve_without_leaking_key(self):
        expert = self.expert(transport=Mock(side_effect=OSError(KEY)))
        with self.assertRaisesRegex(ExpertUnavailable,'reserve retained') as error:
            expert.advise('task','content',1)
        self.assertNotIn(KEY,str(error.exception))
        self.assertGreater(expert.ledger.totals()['micros'],4_000_000)
        with self.assertRaises(ExpertUnavailable):
            expert.advise('task','second request',1)
        self.transport.assert_called_once()

    def test_consultation_excludes_sensitive_and_unrelated_files(self):
        self.files.write('worker.py','password = "nonstandard secret with spaces"\nvalue = 1\n')
        self.files.write('auth.py','PRIVATE_AUTH_DATA')
        self.files.write('unrelated.py','UNRELATED_DATA')
        payload = consultation({'goal':'Repair worker','current_step':{'path':'worker.py','depends_on':['auth.py']}},self.files)
        self.assertIn('value = 1',payload)
        for secret in ('nonstandard secret','PRIVATE_AUTH_DATA','UNRELATED_DATA'):
            self.assertNotIn(secret,payload)

    def test_local_budget_trims_context_but_preserves_goal(self):
        goal='Preserve these exact requirements: double and verify.'
        prompt='Return JSON\nCONTEXT\n'+json.dumps({'ORIGINAL_GOAL':goal,'recent':['x'*50000]})
        packed=pack(prompt,self.cfg)
        self.assertEqual(json.loads(packed.split('\nCONTEXT\n')[1])['ORIGINAL_GOAL'],goal)
        self.assertNotIn('x'*100,packed)
        with self.assertRaisesRegex(ValueError,'essential diagnostics'):
            pack(prompt.replace(goal,'x'*50000),self.cfg)

    def test_progress_indicators_trigger_escalation(self):
        self.assertIsNone(escalation_reason({'repair_streak':1},{}))
        self.assertTrue(escalation_reason({'repair_streak':2},{}))
        self.assertTrue(escalation_reason({'stalled_iterations':3},{}))

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY':KEY})
    def test_controller_resumes_local_worker_after_expert_advice(self):
        self.cfg.update(local_repair_attempts=2,max_iterations=4,cloud={'enabled':True})
        expert = self.expert()
        model = Mock()
        def act(prompt, **kwargs):
            if model.chat.call_count < 3:
                return 'malformed'
            self.assertIn('expert_advice_untrusted',prompt)
            if model.chat.call_count == 3:
                return json.dumps({'type':'PLAN','steps':[{'path':'answer.py','purpose':'Add function','depends_on':[]}]})
            return json.dumps({'type':'WRITE_FILE','path':'answer.py','content':'def answer(): return 42\n'})
        model.chat.side_effect=act
        worker=Controller(self.base,self.cfg,self.store,model,expert=expert)
        task=worker.run(worker.new('demo','Implement an answer function'))
        self.assertEqual(task['completed_steps'],1)
        self.assertEqual(self.files.read('answer.py'),'def answer(): return 42')
        self.transport.assert_called_once()
        memory=self.store.db.execute("SELECT origin,verified FROM memories WHERE category='expert_advice'").fetchone()
        self.assertEqual(tuple(memory),('anthropic',0))

    def test_resume_recovers_committed_write_without_replay(self):
        model=Mock()
        worker=Controller(self.base,self.cfg,self.store,model)
        task=worker.new('demo','Implement answer')
        task.update(plan='saved',steps=[{'path':'answer.py','purpose':'Implement answer','depends_on':[],
            'written':False,'status':'pending'}],iteration=self.cfg['max_iterations'])
        raw=b'def answer(): return 42\n'
        self.files.write('answer.py',raw.decode())
        task['pending']={'type':'WRITE_FILE','path':'answer.py','write_digest':hashlib.sha256(raw).hexdigest()}
        self.store.save_task(task)
        recovered=worker.run(self.store.task(task['id']))
        self.assertTrue(recovered['steps'][0]['written'])
        self.assertIsNone(recovered['pending'])
        model.chat.assert_not_called()

    def test_pause_during_inference_does_not_apply_action(self):
        model=Mock()
        worker=Controller(self.base,self.cfg,self.store,model)
        task=worker.new('demo','Implement answer')
        def pause(prompt, **kwargs):
            worker.interrupt(task['id'],'paused')
            return json.dumps({'type':'PLAN','steps':'write answer.py'})
        model.chat.side_effect=pause
        result=worker.run(task)
        self.assertEqual(result['status'],'paused')
        self.assertFalse(result['plan'])

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY':KEY})
    def test_generated_test_process_does_not_receive_cloud_key(self):
        from engine.runtime import Executor
        self.files.write('test_env.py','import os, unittest\nclass Test(unittest.TestCase):\n'
                         ' def test_key(self): self.assertNotIn("ANTHROPIC_API_KEY", os.environ)\n')
        result=Executor(self.files,self.cfg,lambda _:True).run('python_tests')
        self.assertTrue(result['passed'],result['output'])
        self.assertIn('Ran 1 test',result['stderr'])
        self.assertGreaterEqual(result['duration_seconds'],0)

    def test_http_transport_uses_fixed_origin_and_bearer_header(self):
        with patch('engine.expert.http.client.HTTPSConnection') as connect:
            conn = connect.return_value
            conn.getresponse.return_value.status = 200
            conn.getresponse.return_value.read.return_value = json.dumps(response()).encode()
            self.assertEqual(anthropic_messages({'model':'claude-opus-5-5'},KEY,3),response())
            connect.assert_called_once_with('api.anthropic.com',timeout=3)
            method, path, body, headers = conn.request.call_args.args
            self.assertEqual((method,path),('POST','/v1/messages'))
            self.assertEqual(headers['authorization'],'Bearer '+KEY)
            self.assertNotIn(KEY,body.decode())
            conn.close.assert_called_once()

    def test_lf_model_patch_preserves_windows_line_endings(self):
        path = self.files.path('arithmetic.py')
        path.write_bytes(b'def double(x):\r\n    return (x * 2\r\n')
        self.files.write('arithmetic.py','def double(x):\n    return x * 2',
                         'def double(x):\n    return (x * 2')
        self.assertEqual(path.read_bytes(),b'def double(x):\r\n    return x * 2\r\n')

    def test_interrupted_prepare_does_not_replace_original(self):
        self.files.write('answer.py','value = 1\n')
        with self.assertRaises(RuntimeError):
            self.files.write('answer.py','value = 2','value = 1',
                             Mock(side_effect=RuntimeError('simulated crash before commit')))
        self.assertEqual(self.files.read('answer.py'),'value = 1')

    def test_selects_pytest_and_nested_unittest(self):
        from engine.workflow import test_command
        self.files.write('tests/test_function.py','def test_value(): assert 1 == 1\n')
        self.files.write('tests/test_class.py','import unittest\nclass T(unittest.TestCase): pass\n')
        self.assertEqual(test_command(self.files,['tests/test_function.py']),'python_pytest')
        self.assertEqual(test_command(self.files,['tests/test_class.py']),
                         'python -m unittest discover -s tests -p test*.py -v')

    def test_arithmetic_module_does_not_imply_four_operation_calculator(self):
        from engine.workflow import project_state, verify_requirements
        self.files.write('arithmetic.py','def double(x): return x * 2\n')
        self.files.write('test_arithmetic.py','assert double(-2) == -4\n')
        goal = 'In arithmetic.py make double(-2) return -4'
        result = verify_requirements({'goal':goal}, baseline.verification(goal,'arithmetic.py','return x * 2',
            'test_arithmetic.py','assert double')['checks'],self.files,project_state(self.files))
        self.assertEqual(result['snapshot'],self.files.fingerprint())
