"""Bounded authorization and Git change evidence; attribution is not authentication."""
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
        wf.local(p)
    require(contained(payload['paths'], task['paths']), 'Task paths exceed authorization; amend authorization first')
    require(wf.strings(payload['outputs']), 'Authorization outputs must be a list')
    for ref in payload['outputs']:
        wf.local(ref)
        require(Path(ref).as_posix() == ref, 'Output locations must be canonical paths')
        require(not any(c in ref for c in '*?[]'), 'Output locations must be exact paths without globs')
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


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True)
    require(result.returncode == 0, 'Git change evidence unavailable: a committed Git baseline is required')
    return result.stdout


def starting_revision(root):
    return git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()


def manifest(wf, task):
    revision = task.get('starting_revision')
    require(revision, 'No starting revision; start the task before requesting change evidence')
    before = {}
    before_modes = {}
    check_modes = git(wf.ROOT, 'config', '--get', '--default', 'true', 'core.filemode').strip() == b'true'
    for entry in git(wf.ROOT, 'ls-tree', '-rz', '--full-tree', revision).split(b'\0'):
        if not entry:
            continue
        header, raw_name = entry.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        name = raw_name.decode('utf-8')
        if wf.source_allowed(name):
            require(kind == 'blob' and mode != '120000', 'Source symlinks/submodules are unsupported')
            before[name] = oid
            before_modes[name] = mode
    names = set(before) | {n.decode('utf-8') for n in git(wf.ROOT, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0') if n}
    changes = []
    for name in sorted(names):
        if not wf.source_allowed(name):
            continue
        path = wf.local(name)
        current = git(wf.ROOT, 'hash-object', '--', name).decode().strip() if path.is_file() else None
        old = before.get(name)
        mode = ('100755' if path.stat().st_mode & stat.S_IXUSR else '100644') if path.is_file() else None
        mode_changed = check_modes and mode != before_modes.get(name)
        if current != old or mode_changed:
            changes.append({'path': name, 'status': 'added' if old is None else 'deleted' if current is None else 'modified',
                            'before': old, 'after': current,
                            'before_mode': before_modes.get(name) if check_modes else None, 'after_mode': mode if check_modes else None,
                            'in_scope': wf.coord.covers(task['paths'], name)})
    return {'starting_revision': revision, 'changes': changes,
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
    return result


def evidence(wf, task, config):
    if not bounded(config):
        return None
    return {'authorization': task['authorization'], 'task_class': task.get('task_class', 'standard'),
            'changes': check_changes(wf, task, config), 'outputs': output_manifest(wf, task)}
