import ctypes
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


FIELDS = {
    'WEB_SEARCH': {'query':str},
    'PLAN': {'steps': str},
    'LIST_FILES': {}, 'READ_FILE': {'path': str},
    'SEARCH_FILES': {'query': str}, 'WRITE_FILE': {'path': str, 'content': str},
    'APPLY_PATCH': {'path': str, 'old': str, 'new': str},
    'RUN_TEST': {'command': str}, 'RUN_APPROVED_COMMAND': {'command': str},
    'SAVE_MEMORY': {'content': str}, 'RETRIEVE_MEMORY': {'query': str},
    'COMPLETE_TASK': {'summary': str}, 'REQUEST_APPROVAL': {'reason': str},
    'VERIFY_TASK': {'checks': str},
    'ANALYZE_FAILURE': {'path':str, 'reason':str},
}
COMMANDS = {
    'python_tests': [sys.executable, '-m', 'unittest', 'discover', '-s', '.', '-p', 'test*.py', '-v'],
    'python_pytest': [sys.executable, '-m', 'pytest', '-q'],
    'node_tests': ['node', '--test'],
    'typescript_check': ['tsc', '--noEmit'],
    'php_check': ['php', '-l', 'main.php'],
    'java_check': ['javac', '-proc:none', 'Main.java'],
    'cpp_check': ['g++', '-fsyntax-only', 'main.cpp'],
    'git_status': ['git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', 'status', '--porcelain', '-uno'],
}


def command_spec(command, files):
    """Parse a small argument grammar, never a shell command or arbitrary Python."""
    if not isinstance(command, str) or re.search(r'[;&|<>`$\r\n\x00]', command):
        raise ValueError('Unsafe command. Use python_tests or python_pytest.')
    if command in COMMANDS:
        return COMMANDS[command][:]
    parts = shlex.split(command)
    if parts[:2] in (['python', '-m'], ['python3', '-m']):
        parts = parts[2:]
    if not parts or parts[0] not in {'unittest', 'pytest'}:
        raise ValueError('Unsupported command. Use python_tests or python -m unittest discover; pytest requires installation.')
    runner, *tail = parts
    args = [sys.executable, '-m', runner]
    if runner == 'unittest' and not tail:
        return COMMANDS['python_tests'][:]
    i = 0
    while i < len(tail):
        value = tail[i]
        if value in {'-v', '-q'} or (runner == 'pytest' and value in {'--disable-warnings', '-x'}):
            args.append(value)
        elif runner == 'unittest' and value == 'discover' and i == 0:
            args.append(value)
        elif runner == 'unittest' and value in {'-s', '-t', '-p'} and i+1 < len(tail):
            item = tail[i+1]
            if value == '-p':
                if not re.fullmatch(r'[\w.*?-]+\.py', item):
                    raise ValueError('Unsafe unittest test pattern')
            else:
                files.path(item)
            args.extend([value, item]); i += 1
        elif runner == 'pytest' and value == '--maxfail' and i+1 < len(tail) and tail[i+1].isdigit() and 1 <= int(tail[i+1]) <= 10:
            args.extend([value, tail[i+1]]); i += 1
        elif not value.startswith('-'):
            if runner == 'unittest':
                if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', value):
                    raise ValueError('Invalid unittest module selector')
            else:
                target, *selectors = value.split('::')
                files.path(target)
                if not all(re.fullmatch(r'[A-Za-z_]\w*', s) for s in selectors):
                    raise ValueError('Invalid pytest selector')
            args.append(value)
        else:
            raise ValueError('Unsupported test argument: ' + value)
        i += 1
    return args


def test_runner(args):
    return args[2] if len(args) > 2 and args[1] == '-m' and args[2] in {'unittest', 'pytest'} else 'node' if '--test' in args else None


def tests_passed(result, runner):
    if not result['passed']:
        return False
    text = result['output']
    if runner == 'unittest':
        count = re.search(r'Ran (\d+) tests?', text)
        skipped = re.search(r'skipped=(\d+)', text)
        return bool(count and int(count[1]) > (int(skipped[1]) if skipped else 0) and re.search(r'^OK\b', text, re.M))
    if runner == 'pytest':
        return bool(re.search(r'\b[1-9]\d* passed\b', text))
    return bool(runner == 'node' and re.search(r'(?:# pass|ℹ pass) [1-9]\d*', text))


def parse_action(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or not isinstance(value.get('type'), str) or value.get('type') not in FIELDS:
        raise ValueError('Unknown action')
    fields = FIELDS[value['type']]
    if set(value) != {'type', *fields}:
        raise ValueError('Unexpected or missing action fields')
    for k, kind in fields.items():
        if (value['type'],k) in {('VERIFY_TASK','checks'),('PLAN','steps')} and isinstance(value[k], list) and len(value[k]) <= 100:
            continue
        if type(value[k]) is not kind or len(value[k]) > 100000:
            raise ValueError('Invalid action field: ' + k)
    return value


def action_schema():
    return {'oneOf': [dict(type='object', properties={'type': {'const': name}, **{k: {'type': 'string'} for k in fields}}, required=['type', *fields], additionalProperties=False) for name, fields in FIELDS.items()]}


class Ollama:
    def __init__(self, config):
        self.config = config
        u = urlsplit(config['endpoint'])
        if u.scheme != 'http' or u.hostname not in {'127.0.0.1', 'localhost'} or u.path not in {'', '/'} or u.username or u.query or u.fragment:
            raise ValueError('Only local HTTP Ollama allowed')
        self.port = u.port or 11434
        self.metrics = {}

    def request(self, path, body=None, timeout=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout or self.config['inference_timeout_seconds'])
        try:
            c.request('POST' if body is not None else 'GET', path, body=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json'})
            r = c.getresponse()
            data = r.read(2_000_001)
            if r.status != 200 or len(data) > 2_000_000:
                raise RuntimeError('Ollama HTTP ' + str(r.status) + ': ' + data[:300].decode(errors='replace'))
            return json.loads(data)
        finally:
            c.close()

    def status(self):
        tags = self.request('/api/tags', timeout=5)
        names = [m['name'] for m in tags['models']]
        return {'installed': names, 'selected': self.config['model'], 'ready': self.config['model'] in names, 'running': self.request('/api/ps', timeout=5)}

    def chat(self, prompt, structured=False, timeout=None):
        cfg = self.config
        body = {'model': cfg['model'], 'messages': [{'role': 'user', 'content': prompt}], 'stream': False,
                'keep_alive': cfg['keep_alive'], 'options': {'temperature': cfg['temperature'], 'num_ctx': cfg['context_size'],
                                                          'num_predict': cfg.get('local_output_tokens',1400)}}
        if structured:
            body['format'] = structured if isinstance(structured, dict) else action_schema()
        start = time.monotonic()
        r = self.request('/api/chat', body, timeout)
        self.metrics = {'seconds': round(time.monotonic()-start, 2), 'tokens': r.get('eval_count', 0),
                        'prompt_tokens': r.get('prompt_eval_count'),
                        'tokens_per_second': round(r.get('eval_count', 0)/max(r.get('eval_duration', 0)/1e9, .001), 2)}
        return r['message']['content']


def available_memory_mb():
    if os.name != 'nt':
        return None
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in ('total', 'free', 'page_total', 'page_free', 'virtual_total', 'virtual_free', 'extended')]
    s = Status()
    s.length = ctypes.sizeof(s)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return s.free // (1024*1024)


def tools():
    return {name: shutil.which(name) for name in ('python', 'git', 'node', 'tsc', 'php', 'java', 'javac', 'g++', 'clang++', 'sqlite3')}


def terminate(p):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        import signal
        os.killpg(p.pid, signal.SIGKILL)
    try:
        p.kill()
    except OSError:
        pass
    p.wait(timeout=10)


class Executor:
    def __init__(self, files, config, approve=None, cancelled=None):
        self.files, self.config = files, config
        self.approve = approve or (lambda message: False)
        self.cancelled = cancelled or (lambda: False)
        self.approved = set()
        self.approval_wait_seconds = 0

    def validate(self, command):
        args = command_spec(command, self.files)
        if test_runner(args) == 'pytest' and importlib.util.find_spec('pytest') is None:
            raise ValueError('pytest is unavailable; use Python unittest (python_tests). No package was installed.')
        executable = shutil.which(args[0])
        if not executable:
            raise ValueError('Tool unavailable: ' + args[0])
        args[0] = executable
        self.files.path('.')
        return args

    def run(self, command, timeout=None):
        args = self.validate(command)
        fingerprint = self.files.fingerprint()
        key = (command, fingerprint)
        preapproved = self.config.get('session_approve_tests') and test_runner(args)
        if key not in self.approved and not preapproved:
            approval_start = time.monotonic()
            try:
                approved = self.approve('Execute ' + repr(args) + ' in ' + str(self.files.root) + '\nGenerated code runs with your user permissions (no sandbox). Snapshot: ' + fingerprint[:16])
            finally:
                self.approval_wait_seconds += time.monotonic()-approval_start
            if not approved:
                raise PermissionError('Execution approval denied')
            if self.files.fingerprint() != fingerprint:
                raise PermissionError('Files changed during approval')
            self.approved.add(key)
        inherited = {'PATH','PATHEXT','SYSTEMROOT','WINDIR','SYSTEMDRIVE','COMSPEC',
                     'TEMP','TMP','USERPROFILE','HOME','LOCALAPPDATA','APPDATA',
                     'PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','LANG','LC_ALL'}
        env = {k: v for k, v in os.environ.items() if k.upper() in inherited}
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        limit = self.config['max_output_bytes']
        deadline = time.monotonic() + min(timeout or self.config['command_timeout_seconds'], self.config['command_timeout_seconds'])
        started = time.monotonic()
        if self.cancelled():
            return {'command':command,'returncode':None,'reason':'cancelled','output':'',
                    'stdout':'','stderr':'','duration_seconds':0,'passed':False}
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            p = subprocess.Popen(args, cwd=self.files.root, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                 shell=False, start_new_session=os.name != 'nt')
            reason = None
            try:
                while p.poll() is None:
                    if self.cancelled():
                        reason = 'cancelled'
                    elif time.monotonic() >= deadline:
                        reason = 'timeout'
                    elif os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > limit:
                        reason = 'output limit'
                    if reason:
                        terminate(p)
                        break
                    time.sleep(.05)
            except BaseException:
                terminate(p)
                raise
            if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > limit and reason is None:
                reason = 'output limit'
            out.seek(0)
            err.seek(0)
            raw_error = err.read(limit)
            stdout = out.read(limit-len(raw_error)).decode('utf-8', errors='replace')
            stderr = raw_error.decode('utf-8', errors='replace')
        return {'command': command, 'returncode': p.returncode, 'reason': reason, 'output': stdout+stderr,
                'stdout':stdout, 'stderr':stderr, 'duration_seconds':round(time.monotonic()-started,3),
                'passed': p.returncode == 0 and not reason}
