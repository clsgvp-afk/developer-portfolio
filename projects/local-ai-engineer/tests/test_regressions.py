import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import test_acceptance as original
from engine.runtime import command_spec, parse_action, tests_passed
from engine.workflow import project_state, validate_action, verify_requirements


class Regression(unittest.TestCase):
    setUp = original.Acceptance.setUp
    tearDown = original.Acceptance.tearDown
    executor = original.Acceptance.executor
    controller = original.Acceptance.controller

    def plan(self, goal='Create a calculator with add, subtract, multiply and divide.'):
        c = self.controller(None)
        t = c.new('demo',goal)
        t['plan'] = 'Write calculator.py, test_calculator.py, run tests, verify requirements'
        return t

    def test_multiline_submission_is_atomic(self):
        with patch('builtins.input', side_effect=['Four operations','2','yes','/done','next']) as read:
            self.assertEqual(main.task_description(), 'Four operations\n2\nyes')
            self.assertEqual(read(), 'next')
        with patch('builtins.input',side_effect=['unfinished',EOFError()]):
            with self.assertRaises(ValueError): main.task_description()
        self.assertIsNone(main.parser().parse_args(['task','demo']).goal)

    def test_approval_repeats_until_explicit_answer(self):
        for answer, expected in [('yes',True),('no',False)]:
            with patch('builtins.input',side_effect=['Create a project','y','',answer,'later']) as read, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main.approval('SAME SNAPSHOT'),expected)
                self.assertEqual(output.getvalue().count('SAME SNAPSHOT'),4)
                self.assertEqual(read(),'later')

    def test_order_rejects_empty_tests_and_test_before_source(self):
        task = self.plan()
        for action in ({'type':'RUN_TEST','command':'python_tests'},
                       {'type':'WRITE_FILE','path':'test_calc.py','content':'import unittest'}):
            with self.assertRaises(ValueError): validate_action(task,action,self.files,project_state(self.files))
        self.files.write('calculator.py','def add(a,b): return a+b\n')
        with self.assertRaises(ValueError): validate_action(task,{'type':'RUN_TEST','command':'python_tests'},self.files,project_state(self.files))
        self.files.write('test_calc.py','import unittest\n')
        validate_action(task,{'type':'RUN_TEST','command':'python_tests'},self.files,project_state(self.files))

    def test_original_goal_is_never_truncated_or_replaced_by_memory(self):
        goal = 'Create a calculator.\n' + 'Detailed original requirement. '*140 + 'FINAL REQUIRED DIVISION'
        prompts = []
        class Model:
            def chat(inner,prompt,**kw):
                prompts.append(prompt)
                if len(prompts) == 1: return json.dumps({'type':'PLAN','steps':'Implement original goal'})
                return json.dumps({'type':'REQUEST_APPROVAL','reason':'Need input'})
        self.store.remember('verified_solution','practice solution def solve(items): return sorted(set(items))',True)
        c = self.controller(Model()); c.run(c.new('demo',goal))
        for prompt in prompts:
            ctx = json.loads(prompt.split('\nCONTEXT\n',1)[1])
            self.assertEqual(ctx['ORIGINAL_GOAL'],goal)
            self.assertIn('project_state',ctx)
            self.assertNotIn('sorted(set(items))',prompt)

    def test_unrelated_exercise_is_rejected(self):
        task = self.plan()
        with self.assertRaisesRegex(ValueError,'Unrelated'):
            validate_action(task,{'type':'WRITE_FILE','path':'solution.py','content':'def solve(items): return sorted(set(items))'},self.files,project_state(self.files))
        validate_action(task,{'type':'WRITE_FILE','path':'calculator.py','content':'def add(a,b): return a+b'},self.files,project_state(self.files))
        self.files.write('solution.py','def add(a,b): return a-b\n')
        validate_action(task,{'type':'APPLY_PATCH','path':'solution.py','old':'a-b','new':'a+b'},self.files,project_state(self.files))

    def test_unittest_arguments_execute_without_shell(self):
        self.files.write('calculator.py','def add(a,b): return a+b\n')
        self.files.write('test_calc.py','import unittest\nfrom calculator import add\nclass Tests(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n')
        command='python -m unittest discover -s . -p test_*.py -v'
        result=self.executor().run(command)
        self.assertTrue(tests_passed(result,'unittest'))
        for command in ('python -c pass','pytest -p evil','pytest ../escape','python -m unittest; whoami','pytest --override-ini x=y'):
            with self.assertRaises(ValueError): command_spec(command,self.files)
        self.assertEqual(command_spec('python -m pytest -q test_calc.py',self.files)[1:4],['-m','pytest','-q'])
        with patch('engine.runtime.importlib.util.find_spec',return_value=object()):
            self.assertEqual(self.executor().validate('pytest -q')[2],'pytest')

    def test_completion_requires_requirements_and_current_snapshot(self):
        task = self.plan('Implement addition')
        self.files.write('calculator.py','def add(a,b): return a+b\n')
        self.files.write('test_calc.py','assert 2+3 == 5\n')
        task['verified']=self.files.fingerprint()
        with self.assertRaisesRegex(ValueError,'VERIFY_TASK'):
            validate_action(task,{'type':'COMPLETE_TASK','summary':'done'},self.files,project_state(self.files))
        check=original.verification(task['goal'], 'calculator.py','def add(', 'test_calc.py','assert 2+3')['checks']
        task['acceptance']=verify_requirements(task,check,self.files,project_state(self.files))
        validate_action(task,{'type':'COMPLETE_TASK','summary':'done'},self.files,project_state(self.files))
        with self.assertRaises(ValueError): verify_requirements(task,'[]',self.files,project_state(self.files))
        self.files.write('calculator.py','a-b','a+b')
        with self.assertRaises(ValueError): validate_action(task,{'type':'COMPLETE_TASK','summary':'done'},self.files,project_state(self.files))

    def test_calculator_cannot_complete_with_only_addition(self):
        task=self.plan()
        self.files.write('calculator.py','def add(a,b): return a+b\n')
        self.files.write('test_calc.py','assert add(2,3) == 5\n')
        check=original.verification(task['goal'],'calculator.py','def add(','test_calc.py','assert add')['checks']
        with self.assertRaisesRegex(ValueError,'missing implemented/tested operation'):
            verify_requirements(task,check,self.files,project_state(self.files))

    def test_invalid_action_recovery_and_no_repeat_execution(self):
        model=original.MockModel(
            {'type':'PLAN','steps':'Write calculator and tests'},
            {'type':'RUN_TEST','command':'python_tests'},
            {'type':'RUN_TEST','command':'python_tests'},
            {'type':'WRITE_FILE','path':'calculator.py','content':'def add(a,b): return a+b\n'},
            {'type':'WRITE_FILE','path':'test_calc.py','content':'import unittest\nfrom calculator import add\nclass Tests(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n'},
            {'type':'RUN_TEST','command':'unsupported_runner'},
            {'type':'RUN_TEST','command':'python -m unittest discover -v'},
            original.verification('Implement addition','calculator.py','def add(','test_calc.py'),
            {'type':'COMPLETE_TASK','summary':'Original task verified'})
        c=self.controller(model)
        task=c.run(c.new('demo','Implement addition'))
        self.assertEqual(task['status'],'completed')
        self.assertEqual(self.store.stats()['tasks'],1)
        kinds=[r[0] for r in self.store.db.execute('SELECT kind FROM events WHERE task=?',(task['id'],))]
        self.assertEqual(kinds.count('RUN_TEST'),1)
        self.assertEqual(kinds.count('error'),3)

    def test_existing_project_can_run_baseline_tests(self):
        self.files.write('test_existing.py','import unittest\nclass Tests(unittest.TestCase):\n def test_existing(self): self.assertTrue(True)\n')
        task=self.plan('Analyze existing tests')
        validate_action(task,{'type':'RUN_TEST','command':'python_tests'},self.files,project_state(self.files))

    def test_skipped_tests_do_not_verify_completion(self):
        self.assertFalse(tests_passed({'passed':True,'output':'Ran 2 tests\nOK (skipped=2)'},'unittest'))
        self.assertFalse(tests_passed({'passed':True,'output':'2 skipped'},'pytest'))
        self.assertTrue(tests_passed({'passed':True,'output':'4 passed in 0.2s'},'pytest'))

    def test_discovery_must_match_real_files(self):
        task=self.plan()
        self.files.write('calculator.py','def add(a,b): return a+b\n')
        self.files.write('test_calc.py','import unittest\n')
        with self.assertRaisesRegex(ValueError,'matches no test files'):
            validate_action(task,{'type':'RUN_TEST','command':'python -m unittest discover -p *_tests.py'},self.files,project_state(self.files))

    def test_internal_review_recovers_using_actual_file_excerpts(self):
        self.files.write('calculator.py','def add(a, b): return a + b\n')
        self.files.write('test_calc.py','import unittest\nfrom calculator import add\nclass Tests(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n')
        actions=iter([
            {'type':'PLAN','steps':'Run existing tests and verify addition'},
            {'type':'RUN_TEST','command':'python_tests'},
            original.verification('Implement addition','calculator.py','fabricated nonexistent code','test_calc.py'),
            {'type':'REQUEST_APPROVAL','reason':'Verification evidence does not exist'},
            {'type':'READ_FILE','path':'calculator.py'},
            original.verification('Implement addition','calculator.py','def add(a, b):','test_calc.py'),
            {'type':'COMPLETE_TASK','summary':'Addition verified'}])
        prompts=[]
        class Model:
            def chat(inner,prompt,**kw):
                prompts.append((json.loads(prompt.split('\nCONTEXT\n')[1]),kw['structured']))
                return json.dumps(next(actions))
        c=self.controller(Model()); task=c.run(c.new('demo','Implement addition'))
        self.assertEqual(task['status'],'completed')
        self.assertIn('def add(a, b):',prompts[2][0]['current_file_excerpts']['calculator.py'])
        allowed={s['properties']['type']['const'] for s in prompts[2][1]['oneOf']}
        self.assertNotIn('RUN_TEST',allowed)
        self.assertNotIn('REQUEST_APPROVAL',allowed)

    def test_malformed_type_and_structured_verification(self):
        with self.assertRaises(ValueError): parse_action({'type':[]})
        self.assertEqual(parse_action({'type':'VERIFY_TASK','checks':[]})['checks'],[])


if __name__ == '__main__': unittest.main()
