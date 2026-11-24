"""Small deterministic guards around the model's proposed workflow."""
import ast
import json
from pathlib import Path
import re

from .runtime import command_spec, test_runner


def is_test(name):
    p = Path(name)
    return p.name.startswith('test') and p.suffix == '.py' or p.name.endswith(('_test.py', '.test.js', '.test.ts', '.spec.js', '.spec.ts'))


def project_state(files):
    names = files.listing()
    tests = [n for n in names if is_test(n)]
    sources = [n for n in names if n not in tests and Path(n).suffix in {'.py', '.js', '.ts', '.php', '.java', '.cpp', '.c', '.html', '.css', '.sql'} and Path(n).name != '__init__.py']
    return {'files': names, 'sources': sources, 'tests': tests}


def test_command(files, names):
    python = [name for name in names if name.endswith('.py')]
    if not python:
        return 'node_tests'
    for name in python:
        try:
            tree = ast.parse(files.path(name).read_text(encoding='utf-8-sig'))
        except (SyntaxError, OSError, UnicodeError):
            continue
        if (Path(name).name.endswith('_test.py') and not Path(name).name.startswith('test')) or any(
                isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_') for node in tree.body):
            return 'python_pytest'
    roots = {Path(name).parts[0] if len(Path(name).parts)>1 else '.' for name in python}
    if roots == {'tests'}:
        return 'python -m unittest discover -s tests -p test*.py -v'
    return 'python_tests'


def requirements(goal):
    return list(dict.fromkeys(s.strip(' -\t\r') for s in goal.splitlines() if s.strip(' -\t\r'))) or [goal]


def next_step(task, state):
    if not task['plan']:
        return 'PLAN concrete steps for ORIGINAL_GOAL'
    if not state['sources'] and not (task.get('initial_files') and state['tests']):
        return 'WRITE_FILE source implementing ORIGINAL_GOAL; do not run tests yet'
    if not state['tests']:
        return 'WRITE_FILE relevant automated tests for ORIGINAL_GOAL'
    if not task.get('verified'):
        return 'Run existing tests, or fix source using the latest test failure; then run tests'
    if not task.get('acceptance'):
        return 'VERIFY_TASK: map EVERY original requirement to exact source and test evidence'
    return 'COMPLETE_TASK: report original requirements verified'


def anchors(goal):
    goal = re.sub(r'(?:do not|never|not a|не создавай|не используй)[^.\n]*', '', goal, flags=re.I)
    words = set(re.findall(r'[\w]{3,}', goal.lower()))
    words -= set('create simple python project implement function functions write code source unit tests unittest test with and the for return should must define input output local file files generate use using program tool application backend frontend'.split())
    if words & {'calculator', 'arithmetic', 'калькулятор', 'addition'}:
        words |= {'add', 'subtract', 'multiply', 'divide', 'calc', 'addition', 'subtraction', 'multiplication', 'division'}
    return words


def related(goal, name, content):
    """Reject obvious exercise contamination, not arbitrary legitimate utility modules."""
    text = (name + '\n' + content).lower()
    evidence = set(re.findall(r'[\w]{3,}', text))
    if anchors(goal) & evidence:
        return True
    exercise = re.search(r'def solve\(|sorted\(set\(|fibonacci|factorial|character.{0,15}count', text)
    return not (exercise or Path(name).name == 'solution.py')


def validate_action(task, action, files, state):
    kind = action['type']
    if kind in {'WRITE_FILE', 'APPLY_PATCH', 'RUN_TEST', 'RUN_APPROVED_COMMAND', 'VERIFY_TASK', 'COMPLETE_TASK'} and not task['plan']:
        raise ValueError('Invalid order: PLAN the original task first')
    if kind == 'PLAN' and task['plan']:
        raise ValueError('Plan already saved; follow next_step instead')
    if kind == 'REQUEST_APPROVAL' and task.get('verified') == files.fingerprint() and not task.get('acceptance'):
        raise ValueError('Internal requirement review needs no new approval. Read actual files and correct VERIFY_TASK evidence.')
    if kind in {'WRITE_FILE', 'APPLY_PATCH'}:
        content = action.get('content', action.get('new', ''))
        if kind == 'APPLY_PATCH':
            content = files.path(action['path']).read_text(encoding='utf-8-sig').replace(action['old'], action['new'], 1)
        if not related(task['goal'], action['path'], content):
            raise ValueError('Unrelated exercise/solution rejected. Implement ORIGINAL_GOAL, not a practice problem.')
        if is_test(action['path']) and not state['sources'] and not task.get('initial_files'):
            raise ValueError('Invalid order: create the actual source before its tests')
    if kind in {'RUN_TEST', 'RUN_APPROVED_COMMAND'}:
        args = command_spec(action['command'], files)
        runner = test_runner(args)
        if kind == 'RUN_TEST' and not runner:
            raise ValueError('RUN_TEST requires unittest, pytest or node_tests; build checks use RUN_APPROVED_COMMAND')
        if runner:
            if task.get('verified') == files.fingerprint():
                raise ValueError('This snapshot already passed tests. Use VERIFY_TASK with exact current file evidence next.')
            if not state['tests']:
                raise ValueError('No test files exist. Write relevant tests before RUN_TEST.')
            # Existing standalone test suites may intentionally exercise external behavior.
            if not state['sources'] and not task.get('initial_files'):
                raise ValueError('No source files exist. Implement ORIGINAL_GOAL before RUN_TEST.')
            if runner == 'unittest' and 'discover' in args:
                pattern = args[args.index('-p')+1] if '-p' in args else 'test*.py'
                start = args[args.index('-s')+1] if '-s' in args else '.'
                root = files.path(start)
                if not any(files.path(n).is_relative_to(root) and Path(n).match(pattern) for n in state['tests']):
                    raise ValueError('Selected discovery pattern/path matches no test files. Use python_tests.')
    if kind in {'VERIFY_TASK', 'COMPLETE_TASK'}:
        if not task.get('verified') or task['verified'] != files.fingerprint():
            raise ValueError('Premature completion/verification: run real tests after the last edit')
    if kind == 'COMPLETE_TASK' and (not task.get('acceptance') or task['acceptance']['snapshot'] != files.fingerprint()):
        raise ValueError('VERIFY_TASK must check every ORIGINAL_GOAL requirement before completion')


def verify_requirements(task, checks, files, state):
    checks = json.loads(checks) if isinstance(checks, str) else checks
    expected = requirements(task['goal'])
    if not isinstance(checks, list) or len(checks) != len(expected):
        raise ValueError('checks must be a JSON list with one item per original requirement')
    seen = []
    for item in checks:
        if not isinstance(item, dict) or set(item) != {'requirement', 'evidence'} or item['requirement'] not in expected:
            raise ValueError('Use exact unchanged requirement text from original_requirements')
        seen.append(item['requirement'])
        paths = []
        if isinstance(item['evidence'],dict) and set(item['evidence'])=={'source','test'}:
            item = {**item,'evidence':list(item['evidence'].values())}
        if not isinstance(item['evidence'], list):
            raise ValueError('evidence must be a list of path/text objects')
        for evidence in item['evidence']:
            if not isinstance(evidence, dict) or set(evidence) != {'path', 'text'} or not isinstance(evidence['text'], str) or len(evidence['text'].strip()) < 4:
                raise ValueError('Provide meaningful exact path/text evidence')
            content = files.path(evidence['path']).read_text(encoding='utf-8-sig')
            if evidence['text'] not in content:
                raise ValueError('Verification evidence does not exist in ' + evidence['path'])
            paths.append(evidence['path'])
        if not set(paths) & set(state['sources']) or not set(paths) & set(state['tests']):
            raise ValueError('Each requirement needs source evidence AND tested evidence')
    if set(seen) != set(expected):
        raise ValueError('Some original requirements were omitted')
    goal = task['goal'].lower()
    calculator = re.search(r'\bcalculator\b|калькулятор', goal)
    four_operations = re.search(r'\b(?:four|4)\s+(?:arithmetic\s+)?operations\b|четыр\w*\s+операци', goal)
    named_operations = all(re.search(r'\b'+name+r'\b', goal) for name in ('add','subtract','multiply','divide'))
    if calculator and (four_operations or named_operations):
        source = '\n'.join(files.path(p).read_text(encoding='utf-8-sig') for p in state['sources'])
        tests = '\n'.join(files.path(p).read_text(encoding='utf-8-sig') for p in state['tests'])
        for operation in ('add|addition', 'subtract|sub|subtraction', 'multiply|mul|multiplication', 'divide|div|division'):
            pattern = r'\b(?:' + operation + r')\s*\('
            if not re.search(pattern, source) or not re.search(pattern, tests):
                raise ValueError('Original calculator requirement missing implemented/tested operation: ' + operation)
    return {'snapshot': files.fingerprint(), 'checks': checks}
