"""Minimal live check: one model call writes code, real deterministic tests execute it."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.files import Files
from engine.runtime import Executor, Ollama, parse_action

base = Path(__file__).resolve().parents[1]
cfg = json.loads((base/'config.json').read_text())
report = {'connectivity':False, 'inference':False, 'live_coding':False}
try:
    model = Ollama(cfg)
    report['status'] = model.status()
    report['connectivity'] = True
    if not report['status']['ready']: raise RuntimeError('Selected model missing')
    response = model.chat('Return ONE WRITE_FILE JSON action with path "solution.py" and content containing Python function add(a,b) returning a+b. No explanation.', structured=True)
    a = parse_action(response)
    report['inference'] = True
    if a['type'] != 'WRITE_FILE' or a['path'] != 'solution.py': raise RuntimeError('Unexpected coding action')
    with tempfile.TemporaryDirectory() as tmp:
        files = Files(tmp, base/'data/backups')
        files.write('solution.py', a['content'])
        files.write('test_add.py', 'import unittest\nfrom solution import add\nclass Tests(unittest.TestCase):\n def test_add(self):\n  self.assertEqual(add(2,3),5)\n  self.assertEqual(add(-7,2),-5)\n')
        print(a['content'])
        from main import approval
        result = Executor(files,cfg,approval).run('python_tests')
        report['live_coding'] = result['passed']
        report['test'] = result
    report['metrics'] = model.metrics
    report['gpu'] = model.request('/api/ps')
except (ValueError, RuntimeError, OSError) as exc:
    report['error'] = str(exc)
(base/'data/live_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(report,indent=2,ensure_ascii=False))
sys.exit(0 if all(report[k] for k in ('connectivity','inference','live_coding')) else 1)
