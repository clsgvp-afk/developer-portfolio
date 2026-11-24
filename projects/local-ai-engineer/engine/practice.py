import json
from pathlib import Path
import shutil
import tempfile

from .controller import Controller
from .files import Files
from .runtime import Executor


# Fixed public cases and separate held-out cases; never chosen by the model.
EXERCISES = [
    ('algorithms', 'Define solve(items): return sorted unique integers.', [([3,1,3], [1,3])], [([], []), ([-2,4,-2,0], [-2,0,4])]),
    ('data_structures', 'Define solve(text): return dict mapping each character to its count.', [('aba', {'a':2,'b':1})], [('', {}), ('!!x', {'!':2,'x':1})]),
    ('async', 'Define async solve(items): return sum of integers; must be a coroutine function.', [([1,2],3)], [([],0), ([-9,3],-6)]),
    ('sqlite', 'Define solve(items): use in-memory sqlite3 table to store integers and return their SQL SUM, or 0 for empty input.', [([2,3],5)], [([],0), ([-8,4],-4)]),
    ('rest_api', 'Define solve(request): pure local REST handler. Input dict method,path. GET /health returns {status:200,body:ok}; other paths {status:404,body:not found}; non-GET {status:405,body:method not allowed}. No server required.', [({'method':'GET','path':'/health'},{'status':200,'body':'ok'})], [({'method':'POST','path':'/health'},{'status':405,'body':'method not allowed'}), ({'method':'GET','path':'/x'},{'status':404,'body':'not found'})]),
    ('error_handling', 'Define solve(value): parse integer with int(value); return None on TypeError or ValueError.', [('12',12)], [('x',None), (None,None), ('-9',-9)]),
    ('unit_testing', 'Define solve(n): return n*n. Also create test_solution.py with unittest tests.', [(3,9)], [(0,0), (-7,49)]),
    ('refactoring', 'Refactor to define solve(items): return sum(x*x for x in items if x%2==0), without changing behavior.', [([1,2,3],4)], [([],0), ([-4,3,6],52)]),
]


def test_source(cases, asynchronous=False):
    call = 'asyncio.run(solution.solve(value))' if asynchronous else 'solution.solve(value)'
    return ('import unittest\nimport asyncio\nimport solution\n\nclass FixedTests(unittest.TestCase):\n'
            '    def test_cases(self):\n'
            '        for value, expected in ' + repr(cases) + ':\n'
            '            with self.subTest(value=value):\n'
            '                self.assertEqual(' + call + ', expected)\n')


class Learning:
    def __init__(self, base, config, store, model, approve):
        self.base, self.config, self.store, self.model, self.approve = Path(base), config, store, model, approve
        self.flag = self.base/'data/learn.stop'

    def start(self, count=1):
        if self.flag.exists():
            self.flag.unlink()
        offset = self.store.stats()['practice']['total']
        for index in range(min(max(count, 1), len(EXERCISES))):
            if self.flag.exists():
                break
            name, spec, public, hidden = EXERCISES[(offset+index) % len(EXERCISES)]
            c = Controller(self.base, self.config, self.store, self.model, self.approve,
                           protected={'test_fixed.py'}, stop=self.flag.exists)
            task = c.new('practice/' + name + '_' + str(offset+index),
                         spec + ' Write solution.py; run python_tests. Fixed test_fixed.py is protected. Complete only after tests pass.')
            f = Files(self.base/'workspace'/task['project'], self.base/'data/backups')
            f.write('test_fixed.py', test_source(public, name == 'async'))

            def held_out(files, executor):
                # Evaluator files are created only after the proposed solution passes public tests.
                with tempfile.TemporaryDirectory(prefix='lae_eval_') as tmp:
                    isolated = Files(tmp, self.base/'data/backups')
                    isolated.write('solution.py', files.path('solution.py').read_text(encoding='utf-8'))
                    isolated.write('test_heldout.py', test_source(hidden, name == 'async'))
                    result = Executor(isolated, self.config, self.approve, self.flag.exists).run('python_tests')
                    self.store.event(task['id'], 'heldout_pass' if result['passed'] else 'heldout_fail', json.dumps(result))
                    return result['passed']

            result = c.run(task, evaluator=held_out)
            passed = result['status'] == 'completed'
            self.store.practice_result(name, passed, sum(result['failures'].values()))
            print(name + ': ' + result['status'])
            if result['status'] in {'cancelled', 'needs_approval', 'low_memory'}:
                break
