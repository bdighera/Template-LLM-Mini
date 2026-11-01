"""Bounded authorization and Git change evidence; attribution is not authentication."""
from datetime import datetime
import hashlib
import json
import subprocess
import stat
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def hash_file(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def bounded(config):
    return config.get('governance', {}).get('mode', 'legacy') == 'bounded'


def contained(patterns, requested):
    # Deliberately conservative for globs: never infer arbitrary glob containment.
    return all(any(p == r or (p.endswith('/**') and
                   (r == p[:-3] or r.startswith(p[:-2]))) for p in patterns) for r in requested)


def validate_settings(settings):
    require(set(settings) == {'mode', 'maintenance_approval'}, 'governance: expected mode and maintenance_approval')
    require(settings['mode'] in ('legacy', 'bounded'), 'governance.mode: use legacy or bounded')
    require(isinstance(settings['maintenance_approval'], str) and settings['maintenance_approval'].strip(),
            'governance.maintenance_approval: approval reference or none')


def validate_authorization(wf, task, payload):
    require(isinstance(payload, dict) and set(payload) == {
        'objective', 'criteria', 'paths', 'outputs', 'permissions', 'approval_reference'},
        'Use templates/authorization.json fields')
    require(wf.text(payload['objective']) and wf.text(payload['approval_reference']), 'Objective and approval reference required')
    require(payload['approval_reference'].strip().lower() != 'none', 'Explicit approval reference required')
    require(payload['criteria'] == task['criteria'], 'Authorization criteria must match task criteria exactly')
    require(wf.strings(payload['paths']) and payload['paths'], 'Authorization paths required')
    for p in payload['paths']:
        require(wf.coord.normalize_pattern(p) == p and wf.source_allowed(p), 'Authorization paths must be canonical source patterns')
        # Only the static prefix touches the filesystem; Windows rejects '*' in path lookups.
        static = Path(p).parts[:next((i for i, part in enumerate(Path(p).parts) if any(c in part for c in '*?[]')), None)]
        if static:
            wf.local(Path(*static).as_posix())
    require(contained(payload['paths'], task['paths']), 'Task paths exceed authorization; amend authorization first')
    require(wf.strings(payload['outputs']), 'Authorization outputs must be a list')
    for ref in payload['outputs']:
        require(not any(c in ref for c in '*?[]'), 'Output locations must be exact paths without globs')
        wf.local(ref)
        require(Path(ref).as_posix() == ref, 'Output locations must be canonical paths')
        require(Path(ref).parts[0] == 'data' and len(Path(ref).parts) > 1 and not (ref.casefold() == 'data/raw' or ref.casefold().startswith('data/raw/')),
                'Outputs must be under data/ outside read-only raw/')
    permissions = payload['permissions']
    require(isinstance(permissions, dict) and set(permissions) == {'data_access', 'dependencies', 'external_activity'},
            'Permissions need data_access, dependencies, external_activity')
    require(all(wf.strings(v) for v in permissions.values()), 'Permission values must be lists; [] means none')


def require_authorized(wf, task, config):
    if not bounded(config):
        return
    auth = task.get('authorization')
    require(auth is not None, 'Task is not authorized; record explicit approval with authorize --approved')
    validate_authorization(wf, task, auth)
    require(task.get('task_class', 'standard') == 'standard' or
            config['governance']['maintenance_approval'].strip().lower() != 'none',
            'Maintenance requires an approved standing policy')


def git(root, *args, input=None):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, input=input)
    require(result.returncode == 0, 'Git change evidence unavailable: a committed Git baseline is required')
    return result.stdout


def starting_revision(root):
    return git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()


def names(output):
    return [n.decode('utf-8') for n in output.split(b'\0') if n]


def tree(wf, revision):
    oids, modes = {}, {}
    for entry in names(git(wf.ROOT, 'ls-tree', '-rz', '--full-tree', revision)):
        header, name = entry.split('\t', 1)
        mode, kind, oid = header.split()
        if wf.source_allowed(name):
            require(kind == 'blob' and mode != '120000', 'Source symlinks/submodules are unsupported')
            oids[name], modes[name] = oid, mode
    return oids, modes


def worktree(wf, revision):
    """Current oid/mode of source paths Git reports as differing from revision (one hash-object call)."""
    candidates = set(names(git(wf.ROOT, 'diff', '--name-only', '--no-renames', '-z', revision)))
    candidates |= set(names(git(wf.ROOT, 'ls-files', '--others', '--exclude-standard', '-z')))
    candidates = sorted(n for n in candidates if wf.source_allowed(n))
    present = [n for n in candidates if wf.local(n).is_file()]
    oids = git(wf.ROOT, 'hash-object', '--stdin-paths', input=''.join(n + '\n' for n in present).encode()).decode().split()
    current = dict.fromkeys(candidates, (None, None))
    for name, oid in zip(present, oids):
        current[name] = (oid, '100755' if wf.local(name).stat().st_mode & stat.S_IXUSR else '100644')
    return current


def other_tasks(wf, task):
    state = wf.read(wf.ROOT / '.workflow/state.json')
    return [t for t in state['tasks'] if t['id'] != task['id'] and t['status'] != 'cancelled']


def owner(wf, task, others, name):
    """Another task accounts for a change if it is unfinished, or finished after this task started."""
    started = task.get('started_at')
    for other in others:
        finished = other.get('finished_at')
        if other['status'] == 'done' and not (started and finished and datetime.fromisoformat(finished) >= datetime.fromisoformat(started)):
            continue
        if wf.coord.covers(other['paths'], name):
            return other['id']
    return None


def begin(wf, task):
    """Record the starting point. Pending changes inside other (non-cancelled) tasks' paths become the baseline."""
    if task.get('starting_revision'):
        return
    revision = starting_revision(wf.ROOT)
    others = other_tasks(wf, task)
    task.update(starting_revision=revision, started_at=wf.coord.now().isoformat(), starting_baseline={
        name: list(value) for name, value in worktree(wf, revision).items()
        if any(wf.coord.covers(t['paths'], name) for t in others)})


def manifest(wf, task):
    revision = task.get('starting_revision')
    require(revision, 'No starting revision; start the task before requesting change evidence')
    committed, committed_modes = tree(wf, revision)
    before, before_modes = dict(committed), dict(committed_modes)
    current = worktree(wf, revision)
    for name, (oid, mode) in task.get('starting_baseline', {}).items():
        before[name], before_modes[name] = oid, mode
        current.setdefault(name, (committed.get(name), committed_modes.get(name)))  # Now matches revision again.
    check_modes = git(wf.ROOT, 'config', '--get', '--default', 'true', 'core.filemode').strip() == b'true'
    others = other_tasks(wf, task)
    changes, attributed = [], {}
    for name in sorted(current):
        oid, mode = current[name]
        old = before.get(name)
        if oid == old and not (check_modes and mode != before_modes.get(name)):
            continue
        if not wf.coord.covers(task['paths'], name) and (holder := owner(wf, task, others, name)):
            attributed[name] = holder
            continue
        changes.append({'path': name, 'status': 'added' if old is None else 'deleted' if oid is None else 'modified',
                        'before': old, 'after': oid,
                        'before_mode': before_modes.get(name) if check_modes else None, 'after_mode': mode if check_modes else None,
                        'in_scope': wf.coord.covers(task['paths'], name)})
    return {'starting_revision': revision, 'changes': changes, 'attributed': attributed,
            'outside_scope': [c['path'] for c in changes if not c['in_scope']]}


def output_manifest(wf, task):
    result = {}
    for ref in task['authorization']['outputs']:
        path = wf.local(ref)
        if not path.exists():
            result[ref] = None
            continue
        candidates = [path] if path.is_file() else path.rglob('*')
        for item in candidates:
            name = item.relative_to(wf.ROOT).as_posix()
            item = wf.local(name)
            if item.is_file():
                result[name] = hash_file(item)
    return result


def check_changes(wf, task, config):
    if not bounded(config):
        return None
    require_authorized(wf, task, config)
    result = manifest(wf, task)
    require(not result['outside_scope'], 'Changes outside approved task paths: ' + ', '.join(result['outside_scope']))
    # Other tasks' changes stay out of this task's evidence so their edits never stale it.
    result.pop('attributed')
    return result


def evidence(wf, task, config):
    if not bounded(config):
        return None
    return {'authorization': task['authorization'], 'task_class': task.get('task_class', 'standard'),
            'changes': check_changes(wf, task, config), 'outputs': output_manifest(wf, task)}
