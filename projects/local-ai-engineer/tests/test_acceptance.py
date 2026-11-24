import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.controller import Controller
from engine.files import Files
from engine.practice import EXERCISES, Learning, test_source
from engine.runtime import Executor, Ollama, parse_action
from engine.storage import Store


class MockModel:
    def __init__(self, *actions):
        self.actions = iter(actions)
    def chat(self, *a, **kw):
        return json.dumps(next(self.actions))


def verification(goal, source='solution.py', code='def add(', test='test_solution.py', snippet='assertEqual'):
    return {'type':'VERIFY_TASK', 'checks':json.dumps([{'requirement':goal, 'evidence':[
        {'path':source,'text':code}, {'path':test,'text':snippet}]}])}


class Acceptance(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        memory = patch('engine.controller.available_memory_mb', return_value=4096)
        memory.start()
        self.addCleanup(memory.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        (self.base/'data').mkdir()
        self.cfg = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
        self.store = Store(self.base/'data/test.sqlite3')
        self.files = Files(self.base/'workspace/demo', self.base/'data/backups')
    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()
    def executor(self, approve=True, cancelled=None):
        return Executor(self.files, self.cfg, lambda _: approve, cancelled)
    def controller(self, model):
        return Controller(self.base, self.cfg, self.store, model, lambda _: True)

    def test_03_action_parsing(self):
        self.assertEqual(parse_action('{"type":"LIST_FILES"}')['type'], 'LIST_FILES')
        for a in ({'type':'SHELL','command':'whoami'}, {'type':'WRITE_FILE','path':'a'}, {'type':'LIST_FILES','extra':1}):
            with self.assertRaises(ValueError): parse_action(a)
    def test_04_file_creation(self):
        self.files.write('src/a.py', 'x=1\n')
        self.assertEqual(self.files.read('src/a.py'), 'x=1')
    def test_05_safe_edit_and_backup(self):
        self.files.write('a.py', 'x=1\n')
        self.files.write('a.py', 'x=2', 'x=1')
        self.assertEqual(self.files.read('a.py'), 'x=2')
        self.assertEqual(len(list((self.base/'data/backups').iterdir())), 1)
        with self.assertRaises(ValueError): self.files.write('a.py','destroy')
        with self.assertRaises(ValueError): self.files.write('a.py','x','missing')
    def test_06_command_validation(self):
        e = self.executor()
        self.assertTrue(e.validate('python_tests'))
        for c in ('powershell', 'python -c pass', 'python_tests & whoami', ['python']):
            with self.assertRaises((ValueError, TypeError)): e.validate(c)
    def test_07_timeout(self):
        self.files.write('test_sleep.py', 'import time\ntime.sleep(10)\n')
        r = self.executor().run('python_tests', timeout=.2)
        self.assertEqual(r['reason'], 'timeout')
        self.assertFalse(r['passed'])
    def test_08_persistence(self):
        self.store.remember('knowledge', 'SQLite transactions are atomic', True)
        other = Store(self.base/'data/test.sqlite3')
        try: self.assertEqual(other.retrieve('transactions')[0]['verified'], 1)
        finally: other.db.close()
    def test_09_relevant_retrieval_and_dedup(self):
        for _ in range(2): self.store.remember('error', 'Python sqlite rollback fixes transaction failure', True)
        self.store.remember('note', 'CSS grid layout')
        self.assertEqual(len(self.store.retrieve('sqlite rollback')), 1)
    def test_10_learning_progress(self):
        self.store.practice_result('algorithms', True, 1)
        self.assertEqual(self.store.stats()['practice']['passed'], 1)
        self.assertEqual(len(EXERCISES), 8)
    def test_11_checkpoints(self):
        task = self.controller(MockModel()).new('demo', 'goal')
        self.assertEqual(self.store.task(task['id'])['status'], 'pending')
    def test_12_recovery(self):
        self.cfg['max_iterations'] = 1
        c = self.controller(MockModel({'type':'PLAN','steps':'Read and test'}))
        task = c.new('demo', 'goal')
        task['pending'] = {'type':'WRITE_FILE','path':'a','content':'x'}
        self.store.save_task(task)
        result = c.run(self.store.task(task['id']))
        self.assertEqual(result['plan'], 'Read and test')
        self.assertFalse((self.files.root/'a').exists())
        self.assertIsNone(self.store.task(task['id'])['pending'])
    def test_13_cancel(self):
        self.files.write('test_sleep.py', 'import time\ntime.sleep(10)\n')
        self.assertEqual(self.executor(cancelled=lambda:True).run('python_tests')['reason'], 'cancelled')
        c = self.controller(MockModel()); task = c.new('demo','goal'); c.cancel(task['id'])
        self.assertEqual(self.store.task(task['id'])['status'], 'cancelled')
    def test_14_unauthorized_paths(self):
        for p in ('../outside', 'C:\\Windows\\x', 'a:stream', '.git/config', '.env', 'a/../../outside'):
            with self.assertRaises(ValueError): self.files.write(p, 'x')
    def test_15_end_to_end_with_repair(self):
        model = MockModel(
            {'type':'PLAN','steps':'Write addition and fixed tests; fix errors'},
            {'type':'WRITE_FILE','path':'solution.py','content':'def add(a,b): return a-b\n'},
            {'type':'WRITE_FILE','path':'test_solution.py','content':'import unittest\nfrom solution import add\nclass Test(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n'},
            {'type':'RUN_TEST','command':'python_tests'},
            {'type':'APPLY_PATCH','path':'solution.py','old':'a-b','new':'a+b'},
            {'type':'RUN_TEST','command':'python_tests'},
            verification('Implement addition'),
            {'type':'COMPLETE_TASK','summary':'Addition verified'})
        c = self.controller(model)
        result = c.run(c.new('demo', 'Implement addition'))
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(self.store.retrieve('addition')[0]['verified'])
    def test_no_execution_without_approval(self):
        with self.assertRaises(PermissionError): self.executor(False).run('python_tests')
    def test_zero_tests_cannot_complete(self):
        self.cfg['max_iterations'] = 2
        c = self.controller(MockModel({'type':'RUN_TEST','command':'python_tests'}, {'type':'COMPLETE_TASK','summary':'fake'}))
        result = c.run(c.new('demo', 'Do task'))
        self.assertNotEqual(result['status'], 'completed')
    def test_no_remote_inference(self):
        for url in ('https://api.openai.com', 'http://evil.example', 'http://localhost@evil.example', 'http://127.0.0.1/cloud'):
            with self.assertRaises(ValueError): Ollama({**self.cfg, 'endpoint':url})
    def test_output_limit(self):
        self.cfg['max_output_bytes'] = 1000
        self.files.write('test_output.py', 'print("x"*20000)\n')
        self.assertLessEqual(len(self.executor().run('python_tests')['output']),1000)
    def test_repeat_limit(self):
        c = self.controller(MockModel(*[{'type':'LIST_FILES'}]*4))
        self.assertEqual(c.run(c.new('demo','goal'))['status'], 'loop_detected')
    def test_practice_fixed_evaluation(self):
        self.files.write('solution.py', 'def solve(items): return sorted(set(items))\n')
        self.files.write('test_fixed.py', test_source(EXERCISES[0][3]))
        self.assertTrue(self.executor().run('python_tests')['passed'])
    def test_export_verified_only(self):
        self.store.remember('note','unverified',False)
        self.store.remember('solution','verified result',True)
        p = self.base/'export.jsonl'; self.store.export(p)
        self.assertEqual(len(p.read_text().splitlines()),1)
    def test_complete_learning_cycle(self):
        goal = EXERCISES[0][1] + ' Write solution.py; run python_tests. Fixed test_fixed.py is protected. Complete only after tests pass.'
        model = MockModel({'type':'PLAN','steps':'Solve and verify'},
                          {'type':'WRITE_FILE','path':'solution.py','content':'def solve(items): return sorted(set(items))\n'},
                          {'type':'RUN_TEST','command':'python_tests'},
                          verification(goal, code='def solve(', test='test_fixed.py'),
                          {'type':'COMPLETE_TASK','summary':'Sorted unique solution'})
        Learning(self.base, self.cfg, self.store, model, lambda _:True).start(1)
        self.assertEqual(self.store.stats()['practice']['passed'],1)
        self.assertEqual(self.store.stats()['tasks'],1)
    def test_cancellation_during_model_response(self):
        c = self.controller(None)
        task = c.new('demo','Cancel before write')
        class CancellingModel:
            def chat(inner,*a,**kw):
                c.cancel(task['id'])
                return json.dumps({'type':'WRITE_FILE','path':'unexpected','content':'x'})
        c.model = CancellingModel()
        result = c.run(task)
        self.assertEqual(result['status'],'cancelled')
        self.assertFalse((self.files.root/'unexpected').exists())


if __name__ == '__main__': unittest.main()
