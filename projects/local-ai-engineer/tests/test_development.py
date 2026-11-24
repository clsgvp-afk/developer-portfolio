import json
import unittest
import time
from unittest.mock import patch

import test_acceptance as original
from engine.development import guard, install_plan, python_checks, refresh
from engine.runtime import parse_action
from engine.runtime import Executor
from engine.workflow import project_state,verify_requirements


def step(path, purpose='Implement one bounded module', depends=()):
    return {'path':path,'purpose':purpose,'depends_on':list(depends)}


class Development(unittest.TestCase):
    setUp = original.Acceptance.setUp
    tearDown = original.Acceptance.tearDown
    controller = original.Acceptance.controller

    def test_syntax_and_undefined_imports_without_execution(self):
        self.files.write('broken.py','def broken(:\n')
        self.files.write('scope.py','def f(): return os.getcwd()\n')
        self.files.write('no_execution.py','raise RuntimeError("NEVER EXECUTE")\n')
        self.files.write('bad_scope.py','return 1\n')
        errors=python_checks(self.files)['errors']
        self.assertEqual({e['path'] for e in errors},{'broken.py','scope.py','bad_scope.py'})
        self.assertTrue(any('os' in e['message'] for e in errors))

    def test_local_imports_symbols_duplicates_and_packages(self):
        self.files.write('models.py','class Record: pass\nclass Record: pass\n')
        self.files.write('consumer.py','from models import Missing\nfrom package import helpers\n')
        self.files.write('package/helpers.py','def value(): return 1\n')
        self.files.write('package/use.py','from .helpers import value\n')
        errors=python_checks(self.files)['errors']
        self.assertTrue(any('Duplicate' in e['message'] for e in errors))
        self.assertTrue(any('models.Missing' in e['message'] for e in errors))
        self.assertFalse(any(e['path'].startswith('package/') for e in errors))

    def test_planned_missing_import_is_scheduled(self):
        task={'goal':'Create modules'}
        install_plan(task,[step('consumer.py'),step('helper.py')],self.files)
        self.files.write('consumer.py','from helper import value\n')
        task['steps'][0]['written']=True
        current=refresh(task,self.files)
        self.assertEqual(current['path'],'helper.py')
        self.assertEqual(task['static_errors'][0]['target'],'helper.py')
        self.assertEqual(task['completed_steps'],0)

    def test_plan_paths_cycles_and_required_files(self):
        for steps in ([step('../escape.py')], [step('a.py'),step('a.py')],
                      [step('a.py',depends=['b.py']),step('b.py',depends=['a.py'])]):
            with self.assertRaises(ValueError): install_plan({'goal':'Create modules'},steps,self.files)
        with self.assertRaisesRegex(ValueError,'omits'):
            install_plan({'goal':'Create a.py and b.py'},[step('a.py')],self.files)

    def test_one_module_at_a_time_and_missing_modules_gate(self):
        task={'goal':'Create modules','verified':None}
        install_plan(task,[step('first.js'),step('second.php',depends=['first.js'])],self.files)
        task['current_step']=refresh(task,self.files)
        for action in ({'type':'WRITE_FILE','path':'second.php'}, {'type':'WRITE_FILE','path':'unrelated.js'}, {'type':'RUN_TEST'}, {'type':'COMPLETE_TASK'}, {'type':'REQUEST_APPROVAL'}):
            with self.assertRaises(ValueError): guard(task,action,self.files)
        guard(task,{'type':'WRITE_FILE','path':'first.js'},self.files)

    def test_persistent_steps_separate_from_model_iterations(self):
        self.cfg['max_iterations']=2
        model=original.MockModel({'type':'PLAN','steps':[step('words.py'),step('greetings.py',depends=['words.py']),step('test_greetings.py',depends=['greetings.py'])]},
                                 {'type':'WRITE_FILE','path':'words.py','content':'def normalize(text): return text.strip().lower()\n'})
        c=self.controller(model)
        task=c.new('demo','Create text processing modules')
        # Cancel after the successful write is persisted, before another inference.
        c.stop=lambda: bool((self.store.task(task['id']) or {}).get('completed_steps',0))
        result=c.run(task)
        saved=self.store.task(task['id'])
        self.assertEqual(saved['completed_steps'],1)
        self.assertEqual(saved['iteration'],2)
        self.assertEqual(saved['current_step']['path'],'greetings.py')
        self.assertGreater(saved['iteration_limit'],self.cfg['max_iterations'])
        self.assertEqual(result['status'],'cancelled')

    def test_static_recovery_then_tests_verify_complete(self):
        goal='Create text normalization modules'
        checks={'type':'VERIFY_TASK','checks':[{'requirement':goal,'evidence':[
            {'path':'words.py','text':'def normalize('},{'path':'test_words.py','text':'assertEqual'}]}]}
        model=original.MockModel(
            {'type':'PLAN','steps':[step('words.py'),step('test_words.py',depends=['words.py'])]},
            {'type':'WRITE_FILE','path':'words.py','content':'def normalize(text):\n return text.strip().lower(\n'},
            {'type':'REQUEST_APPROVAL','reason':'Fix syntax for me'},
            {'type':'RUN_TEST','command':'python_tests'},
            {'type':'APPLY_PATCH','path':'words.py','old':'lower(','new':'lower()'},
            {'type':'WRITE_FILE','path':'test_words.py','content':'import unittest\nfrom words import normalize\nclass Tests(unittest.TestCase):\n def test_normalize(self): self.assertEqual(normalize(" A "),"a")\n'},
            {'type':'RUN_TEST','command':'python_tests'},checks,
            {'type':'COMPLETE_TASK','summary':'Original text normalization verified'})
        c=self.controller(model); result=c.run(c.new('demo',goal))
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['completed_steps'],2)
        self.assertGreater(result['iteration'],result['completed_steps'])
        self.assertFalse(result['static_errors'])

    def test_structured_plan_parser(self):
        value={'type':'PLAN','steps':[step('module.py')]}
        self.assertEqual(parse_action(value),value)

    def test_known_syntax_error_forces_patch_not_read_loop(self):
        self.files.write('words.py','def normalize(text):\n return text.lower(\n')
        captured=[]
        actions=iter([{'type':'PLAN','steps':[step('words.py')]},
                      {'type':'APPLY_PATCH','path':'words.py','old':'lower(','new':'lower()'}])
        class Model:
            def chat(inner,prompt,**kw):
                captured.append((json.loads(prompt.split('\nCONTEXT\n')[1]),kw['structured']))
                return json.dumps(next(actions))
        c=self.controller(Model()); task=c.new('demo','Normalize text')
        c.stop=lambda: bool((self.store.task(task['id']) or {}).get('completed_steps',0))
        result=c.run(task)
        self.assertEqual(result['completed_steps'],1)
        self.assertTrue(captured[1][0]['static_errors'])
        self.assertIn('words.py',captured[1][0]['current_file_excerpts'])
        self.assertEqual([s['properties']['type']['const'] for s in captured[1][1]['oneOf']],['APPLY_PATCH'])

    def test_runtime_failure_has_repair_stage_and_can_complete(self):
        goal='Implement addition'
        model=original.MockModel(
            {'type':'PLAN','steps':[step('numbers.py'),step('test_numbers.py',depends=['numbers.py'])]},
            {'type':'WRITE_FILE','path':'numbers.py','content':'def add(a,b): return a-b\n'},
            {'type':'WRITE_FILE','path':'test_numbers.py','content':'import unittest\nfrom numbers import add\nclass Test(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n'},
            {'type':'RUN_TEST','command':'python_tests'},
            {'type':'ANALYZE_FAILURE','path':'numbers.py','reason':'Original goal requires addition, source subtracts'},
            {'type':'APPLY_PATCH','path':'numbers.py','old':'a-b','new':'a+b'},
            {'type':'RUN_TEST','command':'python_tests'},
            original.verification(goal,'numbers.py','def add(','test_numbers.py'),
            {'type':'COMPLETE_TASK','summary':'Addition verified'})
        c=self.controller(model); result=c.run(c.new('demo',goal))
        self.assertEqual(result['status'],'completed')
        self.assertIsNone(result['test_failure'])
        self.assertEqual(result['repair_analysis']['path'],'numbers.py')

    def test_read_limit_applies_to_snapshot_not_entire_history(self):
        self.cfg['max_iterations']=6
        self.files.write('a.py','value=1\n')
        read={'type':'READ_FILE','path':'a.py'}
        model=original.MockModel({'type':'PLAN','steps':'Inspect existing sources'},read,read,read,
                                 {'type':'WRITE_FILE','path':'other.py','content':'other=2\n'},read)
        c=self.controller(model); result=c.run(c.new('demo','Inspect source files'))
        self.assertEqual(result['status'],'iteration_limit')
        count=self.store.db.execute("SELECT COUNT(*) FROM events WHERE task=? AND kind='READ_FILE'",(result['id'],)).fetchone()[0]
        self.assertEqual(count,4)

    def test_no_op_patch_cannot_hide_failed_snapshot(self):
        self.files.write('a.py','value=1\n')
        with self.assertRaisesRegex(ValueError,'No-op'):
            self.files.write('a.py','value=1','value=1')

    def test_human_approval_wait_is_measured_separately(self):
        self.files.write('test_wait.py','import unittest\nclass Test(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        offset=[0.0]; monotonic=time.monotonic
        def approve(_):
            offset[0]+=1000
            return True
        executor=Executor(self.files,self.cfg,approve)
        with patch('engine.runtime.time.monotonic',side_effect=lambda:monotonic()+offset[0]):
            result=executor.run('python_tests')
        self.assertTrue(result['passed'])
        self.assertGreaterEqual(executor.approval_wait_seconds,1000)

    def test_ready_modules_force_tests_then_requirement_review(self):
        goal='Implement addition'
        self.files.write('calc.py','def add(a,b): return a+b\n')
        self.files.write('test_calc.py','import unittest\nfrom calc import add\nclass Test(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n')
        model=original.MockModel({'type':'PLAN','steps':[step('calc.py'),step('test_calc.py',depends=['calc.py'])]},
            {'type':'READ_FILE','path':'calc.py'},{'type':'READ_FILE','path':'test_calc.py'},
            {'type':'RUN_TEST','command':'python_tests'},original.verification(goal,'calc.py','def add(','test_calc.py'),
            {'type':'COMPLETE_TASK','summary':'Requirements verified'})
        captured=[]
        class Capture:
            def chat(inner,*args,**kw):
                captured.append(kw['structured'])
                return model.chat(*args,**kw)
        c=self.controller(Capture()); result=c.run(c.new('demo',goal))
        self.assertEqual(result['status'],'completed')
        self.assertEqual([s['properties']['type']['const'] for s in captured[3]['oneOf']],['RUN_TEST'])
        self.assertEqual([s['properties']['type']['const'] for s in captured[4]['oneOf']],['VERIFY_TASK'])

    def test_structured_source_and_test_evidence(self):
        self.files.write('calc.py','def add(a,b): return a+b\n')
        self.files.write('test_calc.py','assert add(2,3) == 5\n')
        task={'goal':'Implement addition'}
        checks=[{'requirement':task['goal'],'evidence':{
            'source':{'path':'calc.py','text':'def add(a,b)'},
            'test':{'path':'test_calc.py','text':'assert add(2,3) == 5'}}}]
        result=verify_requirements(task,checks,self.files,project_state(self.files))
        self.assertEqual(result['snapshot'],self.files.fingerprint())


if __name__=='__main__': unittest.main()
