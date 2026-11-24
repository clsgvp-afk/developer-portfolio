import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from engine.controller import Controller
from engine.files import Files
from engine.practice import Learning
from engine.runtime import Ollama, available_memory_mb, tools
from engine.storage import Store
from engine.expert import AnthropicExpert, CloudLedger, dollars
from engine.research import GeminiAdviser, web_search

BASE = Path(__file__).resolve().parent


def approval(message, prompt='Approve this command and source snapshot? [yes/no] '):
    while True:
        print('\n' + message)
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            return False
        if answer in {'yes', 'no'}:
            return answer == 'yes'
        print('Invalid response. Enter exactly yes or no.')


def task_description(initial=None):
    print('Enter task description; finish with /done on its own line.')
    lines = [] if initial is None else [initial]
    while True:
        try:
            line = input()
        except EOFError:
            raise ValueError('Task not submitted: /done required')
        if line.strip() == '/done':
            goal = '\n'.join(lines).strip()
            if not goal:
                raise ValueError('Task description is empty')
            if len(goal) > 6000:
                raise ValueError('Task description exceeds 6000 characters')
            return goal
        lines.append(line)


def show(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def dispatch(args, config, store, model):
    config = {**config, 'session_approve_tests': getattr(args,'approve_tests',False),
              'session_web': getattr(args,'web',False), 'session_free_ai':getattr(args,'free_ai',False)}
    cloud = config.get('cloud', {})
    ledger = CloudLedger(store.db)
    cap = getattr(args, 'cloud_budget', None)
    if cap is not None and config['session_free_ai']:
        raise ValueError('--free-ai selects Gemini for this invocation and cannot be combined with --cloud-budget')
    if cap is not None and not cloud.get('enabled'):
        raise ValueError('Enable cloud explicitly in config.json before authorizing spending')
    expert = None
    if config['session_free_ai']:
        expert = GeminiAdviser(config.get('gemini_model','gemini-3.1-flash-lite'))
    elif cloud.get('enabled') and args.command in {'task','resume'}:
        if cloud.get('provider','anthropic') != 'anthropic':
            raise ValueError('Unsupported cloud provider')
        expert = AnthropicExpert(cloud, ledger, lambda message: approval(message,'Authorize this paid request and data disclosure? [yes/no] '),
                                 uuid.uuid4().hex, cap)
    c = Controller(BASE, config, store, model, approval, expert=expert)
    if args.command == 'search':
        show(web_search(args.query))
    elif args.command == 'status':
        result = {'tools': tools(), 'free_ram_mb': available_memory_mb(), 'hard_process_memory_limit': False,
                  'sandbox': False, 'active_provider':'ollama', 'cloud_enabled':bool(cloud.get('enabled')),
                  'cloud_today':ledger.totals(), 'cloud_daily_budget_usd':cloud.get('daily_budget_usd',5),
                  'config':{name:config[name] for name in ('model','endpoint','context_size','keep_alive','max_iterations',
                          'max_complex_iterations','task_timeout_seconds','session_timeout_seconds') if name in config}}
        try:
            result['ollama'] = model.status()
        except OSError as exc:
            result['ollama_error'] = str(exc)
        show(result)
    elif args.command == 'chat':
        print(model.chat(args.message))
    elif args.command == 'task':
        t = c.new(args.project, args.goal if args.goal is not None else task_description())
        show(c.run(t))
    elif args.command == 'resume':
        t = store.task(args.id)
        if not t:
            raise ValueError('No saved task')
        if t['project'].startswith('practice/'):
            raise ValueError('Practice uses fixed evaluators: use learn start for next exercise')
        # Resume retains prior iteration/time/retry budgets.
        show(c.run(t))
    elif args.command == 'history':
        show(store.history())
    elif args.command in {'cancel','stop','pause'}:
        c.interrupt(args.id, 'paused' if args.command == 'pause' else 'cancelled')
        print('Request saved. In-flight model calls return or time out before the worker stops.')
    elif args.command == 'inspect':
        task = store.task(args.id)
        if not task:
            raise ValueError('No saved task')
        show({'task':task, 'requested_control':store.control(task['id']),
              'events':[dict(r) for r in store.db.execute('SELECT kind,detail,created FROM events WHERE task=? ORDER BY id DESC LIMIT 12',(task['id'],))]})
    elif args.command == 'cloud':
        today = ledger.totals()
        budget = dollars(cloud.get('daily_budget_usd',5))
        show({'enabled':bool(cloud.get('enabled')), 'model':cloud.get('model','claude-opus-5-5'),
              'today':today, 'remaining_usd':max(0,budget-today['micros'])/1_000_000,
              'requests':[dict(r) for r in store.db.execute('SELECT day,task,model,status,reserved,actual,usage FROM cloud_usage ORDER BY id DESC LIMIT 12')]})
    elif args.command == 'memory':
        show(store.retrieve(args.query))
    elif args.command == 'export':
        destination = Files(BASE/'data', BASE/'data/backups').path(args.name)
        store.export(destination)
        print(destination)
    elif args.command == 'init':
        print('SQLite initialized; no dependencies required')
    elif args.command == 'learn':
        learning = Learning(BASE, config, store, model, approval)
        if args.action == 'start':
            learning.start(args.count)
        elif args.action == 'stop':
            learning.flag.touch()
            print('Stop requested; in-flight inference ends by its configured timeout')
        elif args.action in {'stats', 'status'}:
            show(store.stats())
        elif args.action == 'history':
            show([dict(r) for r in store.db.execute('SELECT * FROM practice ORDER BY id DESC LIMIT 30')])
        elif args.action == 'reset':
            if input('Type RESET to clear practice progress only: ') == 'RESET':
                with store.db:
                    store.db.execute('DELETE FROM practice')
                print('Practice progress reset')


def parser():
    p = argparse.ArgumentParser(description='LOCAL AI ENGINEER — Ollama with optional Anthropic advice')
    sub = p.add_subparsers(dest='command')
    for name in ('status', 'history', 'init', 'cloud'):
        sub.add_parser(name)
    chat = sub.add_parser('chat'); chat.add_argument('message')
    search = sub.add_parser('search'); search.add_argument('query')
    task = sub.add_parser('task'); task.add_argument('project'); task.add_argument('goal', nargs='?')
    resume = sub.add_parser('resume'); resume.add_argument('id', nargs='?')
    for name in ('cancel','stop','pause','inspect'):
        control = sub.add_parser(name)
        control.add_argument('id', **({'nargs':'?'} if name == 'inspect' else {}))
    def budget(value):
        try:
            return dollars(value)
        except (ArithmeticError,ValueError):
            raise argparse.ArgumentTypeError('Expected a USD amount from 0 to 10000') from None
    for command in (task,resume):
        command.add_argument('--web', action='store_true', help='Allow bounded public search queries to DuckDuckGo')
        command.add_argument('--free-ai', action='store_true',
                             help='Allow selected context to Gemini using GEMINI_API_KEY from a Free Tier project without billing')
        command.add_argument('--approve-tests',action='store_true',
                             help='Authorize allowlisted tests of evolving generated code for this invocation (user permissions)')
        command.add_argument('--cloud-budget',type=budget,metavar='USD',
                             help='Explicitly authorize paid Anthropic requests and selected project excerpts for this invocation')
    memory = sub.add_parser('memory'); memory.add_argument('query')
    export = sub.add_parser('export'); export.add_argument('name', nargs='?', default='verified.jsonl')
    learn = sub.add_parser('learn'); learn.add_argument('action', choices=['start','stop','status','history','stats','reset']); learn.add_argument('--count', type=int, default=1)
    return p


def main():
    (BASE/'data').mkdir(exist_ok=True)
    config = json.loads((BASE/'config.json').read_text(encoding='utf-8'))
    store, model = Store(BASE/'data/memory.sqlite3'), Ollama(config)
    p = parser()
    args = p.parse_args()
    # Cancellation commands may run while the worker owns the inference lock.
    controls = args.command in {'cancel','pause','stop','inspect','cloud','history','status','search'} or (args.command == 'learn' and args.action in {'stop','status','history','stats'})
    lock = (BASE/'data/worker.lock').open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    if not controls:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print('Another worker is active. Use cancel ID or learn stop.'); return 1
    try:
        if args.command:
            dispatch(args, config, store, model)
        else:
            print('LOCAL AI ENGINEER | Ollama\n1 Chat  2 Coding task  3 Resume  4 Learn  5 Memory  6 History  7 Status  8 Task details  9 Cloud budget  0 Exit')
            while True:
                choice = input('> ').strip()
                if choice in {'0','exit','quit'}:
                    break
                if choice == '1':
                    a = argparse.Namespace(command='chat', message=input('Message: '))
                elif choice == '2':
                    project = input('Project name: ')
                    a = argparse.Namespace(command='task', project=project, goal=task_description())
                elif choice == '3' or choice.lower() in {'continue my previous project', 'продолжить'}:
                    a = argparse.Namespace(command='resume', id=None)
                elif choice == '4' or choice.lower() in {'start coding practice', 'начать обучение'}:
                    a = argparse.Namespace(command='learn', action='start', count=1)
                elif choice == '5':
                    a = argparse.Namespace(command='memory', query=input('Search: '))
                elif choice == '6':
                    a = argparse.Namespace(command='history')
                elif choice == '7':
                    a = argparse.Namespace(command='status')
                elif choice == '8':
                    a = argparse.Namespace(command='inspect', id=None)
                elif choice == '9':
                    a = argparse.Namespace(command='cloud')
                else:
                    print('Choose a menu number. For a multiline task choose 2; finish with /done.')
                    continue
                try:
                    dispatch(a, config, store, model)
                except (ValueError, OSError, RuntimeError) as exc:
                    print('Error:', exc)
    except (ValueError, OSError, RuntimeError, EOFError) as exc:
        print('Error:', exc); return 1
    except KeyboardInterrupt:
        print('\nStopped; task checkpoint retained')
    finally:
        store.db.close()
        lock.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
