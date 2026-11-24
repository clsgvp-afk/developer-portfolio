"""Local-only smoke check with an isolated project and a reopened checkpoint."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import BASE, approval
from engine.controller import Controller
from engine.runtime import Ollama
from engine.storage import Store


def main():
    config = json.loads((BASE/'config.json').read_text(encoding='utf-8'))
    config.update(cloud={'enabled':False}, task_timeout_seconds=300, max_iterations=14)
    with TemporaryDirectory(prefix='lae-hybrid-') as root:
        base = Path(root)
        (base/'data').mkdir()
        database = base/'data/memory.sqlite3'
        store = Store(database)
        project = base/'workspace/smoke'
        worker = Controller(base,config,store,Ollama(config))
        task = worker.new('smoke','Repair arithmetic.py so double(x) returns x * 2.\n'
                          'Create test_arithmetic.py with unittest assertions for double(3) == 6 and double(-2) == -4. '
                          'Plan exactly these two modules, run python_tests, verify and complete.')
        (project/'arithmetic.py').write_text('def double(x):\n    return (x * 2\n',encoding='utf-8')
        def pause_after_first_file():
            if store.task(task['id'])['completed_steps']:
                store.control(task['id'],'paused')
                return True
            return False
        worker.stop = pause_after_first_file
        paused = worker.run(task)
        print('FIRST CHECKPOINT', paused['status'], paused['completed_steps'], flush=True)
        store.db.close()
        if paused['status'] != 'paused':
            return 1
        store = Store(database)
        def review(message):
            for path in sorted(project.glob('*.py')):
                print(path.name, path.read_text(encoding='utf-8'), flush=True)
            return approval(message)
        try:
            resumed = Controller(base,config,store,Ollama(config),review).run(store.task(task['id']))
            events = [dict(r) for r in store.db.execute('SELECT kind,detail FROM events WHERE task=? ORDER BY id',(task['id'],))]
            result = {'checkpoint':paused['status'], 'checkpoint_steps':paused['completed_steps'],
                      'final':resumed['status'], 'iterations':resumed['iteration'], 'steps':resumed['steps'],
                      'tests':[e['detail'] for e in events if e['kind'] in {'test_pass','test_fail'}],
                      'actions':[e['kind'] for e in events], 'requirements_verified':len((resumed['acceptance'] or {}).get('checks',[])),
                      'cloud_requests':0}
            (BASE/'data/hybrid-live-report.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
            print(json.dumps({k:v for k,v in result.items() if k!='actions'},ensure_ascii=False),flush=True)
            return int(resumed['status']!='completed')
        finally:
            store.db.close()


if __name__ == '__main__':
    sys.exit(main())
