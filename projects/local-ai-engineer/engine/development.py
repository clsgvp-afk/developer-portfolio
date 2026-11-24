"""Persistent module scheduling and non-executing Python checks; no framework rules."""
import ast
import builtins
import importlib.machinery
import json
from pathlib import Path
import re
import symtable
import sys


def install_plan(task, value, files, max_steps=24):
    if isinstance(value, str):
        # Read old checkpoints and legacy PLAN responses without resetting their history.
        paths = list(dict.fromkeys(re.findall(r'[\w./-]+\.(?:py|js|ts|php|java|cpp|html|css|sql)\b', value)))
        value = [{'path':p, 'purpose':'Implement the original task in this module', 'depends_on':[]} for p in paths]
    if not isinstance(value, list) or len(value) > max_steps:
        raise ValueError('PLAN steps must be a bounded list of path/purpose/depends_on objects')
    steps = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'path','purpose','depends_on'}:
            raise ValueError('Each step needs path, purpose and depends_on')
        if not isinstance(item['path'],str) or not isinstance(item['purpose'],str) or not item['purpose'].strip() or not isinstance(item['depends_on'],list) or not all(isinstance(p,str) for p in item['depends_on']):
            raise ValueError('Invalid module step')
        files.path(item['path'])
        if any(s['path'].casefold() == item['path'].casefold() for s in steps):
            raise ValueError('Duplicate planned path')
        steps.append({**item, 'status':'pending', 'written':False})
    paths = {s['path'] for s in steps}
    if steps:
        positive_goal = re.sub(r'(?:do not|never|not a|не создавай)[^.\n]*','',task['goal'],flags=re.I)
        requested = set(re.findall(r'[\w./-]+\.(?:py|js|ts|php|java|cpp|html|css|sql)\b',positive_goal))
        if not requested <= paths:
            raise ValueError('Plan omits original requested files: '+', '.join(sorted(requested-paths)))
    ready = set()
    while len(ready) < len(steps):
        added = {s['path'] for s in steps if set(s['depends_on']) <= ready}
        if added <= ready:
            raise ValueError('Plan contains missing or cyclic dependencies')
        ready |= added
    task['steps'] = steps
    task['completed_steps'] = 0


def python_checks(files, planned=()):
    """Inspect AST/symbol tables only. Never import or execute project modules."""
    names = [n for n in files.listing() if n.endswith('.py')]
    known = {str(Path(n).with_suffix('')).replace('\\','/').replace('/', '.').removesuffix('.__init__'):n for n in [*names,*planned] if n.endswith('.py')}
    trees, errors, imported_targets = {}, [], {}
    def error(path, message, target=None):
        errors.append({'path':path,'message':message, **({'target':target} if target else {})})
    for name in names:
        try:
            text = files.path(name).read_text(encoding='utf-8-sig')
            tree = ast.parse(text, filename=name)
            compile(tree, name, 'exec')  # compile only; also rejects return/break outside valid scopes
            trees[name] = tree
        except (SyntaxError, UnicodeError, OSError) as exc:
            error(name, 'Syntax/read error: ' + str(exc)); continue
        declarations = [n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
        duplicates = sorted({n for n in declarations if declarations.count(n)>1})
        if duplicates:
            error(name, 'Duplicate top-level definitions: ' + ', '.join(duplicates))
        table = symtable.symtable(text, name, 'exec')
        globals_known = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported() or s.is_namespace()} | set(dir(builtins)) | {'__name__','__file__','__package__','__doc__','__builtins__','__annotations__','__spec__','__loader__','__cached__'}
        undefined = set()
        def inspect_scope(scope):
            for symbol in scope.get_symbols():
                if symbol.is_referenced() and symbol.is_global() and symbol.get_name() not in globals_known:
                    undefined.add(symbol.get_name())
            for child in scope.get_children(): inspect_scope(child)
        inspect_scope(table)
        if undefined:
            error(name, 'Undefined global names (possible missing imports): ' + ', '.join(sorted(undefined)))
    for name, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node,(ast.Import,ast.ImportFrom)): continue
            if isinstance(node,ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                package = str(Path(name).parent).replace('\\','/').replace('/', '.').strip('.')
                if node.level:
                    parts = package.split('.') if package else []
                    if node.level > len(parts):
                        error(name, f'Invalid relative import at line {node.lineno}'); continue
                    package = '.'.join(parts[:len(parts)-node.level+1])
                    module = '.'.join(p for p in (package,node.module) if p)
                else:
                    module = node.module or ''
                modules = [module] if module else [alias.name for alias in node.names]
                if module and (module in known or any(k.startswith(module+'.') for k in known)):
                    for alias in node.names:
                        child = module+'.'+alias.name
                        if child in known: modules.append(child)
            for module in modules:
                top = module.split('.')[0]
                local = module in known or top in known or any(k.startswith(top+'.') for k in known) or (isinstance(node,ast.ImportFrom) and node.level)
                if local:
                    target = known.get(module)
                    is_package = any(k.startswith(module+'.') for k in known)
                    if target:
                        imported_targets.setdefault(name,[]).append(target)
                        if not files.path(target).is_file(): error(name, 'Unresolved local import: '+module, target)
                    elif not is_package:
                        error(name, 'Unresolved local import: '+module)
                    if isinstance(node,ast.ImportFrom) and module == modules[0] and target in trees:
                        exports = set()
                        for n in trees[target].body:
                            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): exports.add(n.name)
                            elif isinstance(n,(ast.Import,ast.ImportFrom)): exports.update(a.asname or a.name.split('.')[0] for a in n.names)
                            elif isinstance(n,(ast.Assign,ast.AnnAssign)):
                                for t in (n.targets if isinstance(n,ast.Assign) else [n.target]):
                                    exports.update(x.id for x in ast.walk(t) if isinstance(x,ast.Name))
                        for alias in node.names:
                            if alias.name != '*' and alias.name not in exports and module+'.'+alias.name not in known:
                                error(name, 'Unresolved local imported symbol: '+module+'.'+alias.name)
                elif top not in sys.stdlib_module_names and importlib.machinery.PathFinder.find_spec(top) is None:
                    error(name, 'Unresolved import (missing local module or dependency): '+module)
    return {'errors':errors, 'imports':imported_targets}


def refresh(task, files):
    steps = task.setdefault('steps',[])
    diagnostics = python_checks(files, [s['path'] for s in steps])
    task['static_errors'] = diagnostics['errors']
    done = set()
    for _ in range(len(steps)+1):
        for step in steps:
            exists = files.path(step['path']).is_file()
            valid = not any(e['path'] == step['path'] for e in diagnostics['errors'])
            step['status'] = 'completed' if exists and step['written'] and valid and set(step['depends_on']) <= done else 'pending'
            if step['status'] == 'completed': done.add(step['path'])
    task['completed_steps'] = len(done)
    # Repair syntax/names first; unresolved imports into pending modules instead schedule those modules.
    repair = next((e for e in diagnostics['errors'] if not e.get('target')), None)
    if repair:
        return {'path':repair['path'], 'purpose':'Repair static validation errors', 'repair':True}
    for error in diagnostics['errors']:
        if error.get('target'):
            candidate = next((s for s in steps if s['path']==error['target'] and s['status']!='completed' and set(s['depends_on']) <= done), None)
            if candidate: return candidate
    pending = next((s for s in steps if s['status']!='completed' and set(s['depends_on']) <= done), None)
    if pending: return pending
    if task.get('test_failure') and task['test_failure'].get('snapshot',files.fingerprint()) == files.fingerprint():
        output = task['test_failure']['output'].replace('\\','/')
        frames = re.findall(r'File "([^"]+)"',output)
        candidates = list(dict.fromkeys(n for frame in reversed(frames) for n in files.listing() if frame.endswith(n.replace('\\','/'))))
        candidates += [s['path'] for s in steps if s['path'] not in candidates]
        if candidates:
            return {'path':candidates[0], 'purpose':'Repair failed tests against ORIGINAL_GOAL. Correct faulty code or incorrect initial test expectations; never remove required behavior or assertions.',
                    'repair':True,'runtime':True,'repair_candidates':candidates,'depends_on':[s['path'] for s in steps if s['path']!=candidates[0]]}
    return None


def guard(task, action, files):
    kind = action['type']
    if kind == 'REQUEST_APPROVAL':
        raise ValueError('No executable command is pending. Recover internally using current_step/static_errors; execution approval is requested only by the executor.')
    current = task.get('current_step')
    if kind in {'WRITE_FILE','APPLY_PATCH'} and task.get('steps'):
        allowed = {s['path'] for s in task['steps']}
        if action['path'] not in allowed and not any(e['path']==action['path'] for e in task.get('static_errors',[])):
            raise ValueError('Unplanned module rejected: follow the persisted module plan')
        candidates = current.get('repair_candidates',[current['path']]) if current else []
        if current and action['path'] not in candidates:
            raise ValueError('Develop one logical module at a time. Current module: '+current['path'])
    if kind in {'RUN_TEST','VERIFY_TASK','COMPLETE_TASK','RUN_APPROVED_COMMAND'}:
        if task.get('static_errors'):
            raise ValueError('Repair static errors before execution/completion: '+json.dumps(task['static_errors'][:5]))
        if current:
            raise ValueError('Complete planned module before execution/completion: '+current['path'])
        missing = [s['path'] for s in task.get('steps',[]) if not files.path(s['path']).is_file()]
        if missing: raise ValueError('Planned modules are missing: '+', '.join(missing))
