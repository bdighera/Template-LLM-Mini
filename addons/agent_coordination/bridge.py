"""Serialize calls to the dependency-free authoritative workflow; no shell commands."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import workflow as wf
import coordination as coord

LOCK = threading.RLock()


def git(directory, *args):
    """Run a verification command; failures and timeouts are workflow errors (HTTP 409), not crashes."""
    try:
        result = subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True,
                                encoding='utf-8', timeout=10)
    except subprocess.SubprocessError as exc:
        raise ValueError('Worktree Git verification failed') from exc
    wf.require(result.returncode == 0, 'Worktree Git verification failed')
    return result.stdout.strip()


class Bridge:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not (self.root / '.workflow').resolve().is_relative_to(self.root):
            raise ValueError('Workflow state must be local to the authoritative checkout')
        if not (self.root / '.workflow/config.json').is_file():
            raise ValueError('A configured authoritative workflow checkout is required')

    @contextlib.contextmanager
    def locked(self):
        # workflow.py has module globals: serialize them even across multiple apps.
        with LOCK, coord.locked(self.root):
            previous = wf.ROOT, wf.ADDON, wf.AGENT
            wf.ROOT, wf.ADDON = self.root, self.root / 'addons/data_analysis'
            try:
                yield
            finally:
                wf.ROOT, wf.ADDON, wf.AGENT = previous

    def task(self, task_id):
        state = wf.read(self.root / '.workflow/state.json')
        config = wf.read(self.root / '.workflow/config.json')
        wf.validate_config(config)
        wf.require(state.get('schema_version') == wf.VERSION, 'Unsupported workflow state')
        task = next((t for t in state['tasks'] if t['id'] == task_id), None)
        wf.require(task is not None, 'Unknown workflow task')
        return state, config, task

    def snapshot(self, config, task):
        selected = list(task['source_files'])
        for pattern in task['paths']:
            for path in wf.ROOT.glob(pattern):
                ref = path.relative_to(wf.ROOT).as_posix()
                if wf.source_allowed(ref) and path.exists():
                    selected.append(ref)
        return wf.fingerprint(config, dict(task, source_files=sorted(set(selected))))

    def stamp(self, task_id):
        _, config, task = self.task(task_id)
        snapshot = self.snapshot(config, task)
        value = dict(task=task, config=config, source=snapshot['hash'])
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def location(self, assignment):
        # Approvals and the service run from different directories; a relative path would differ between them.
        wf.require(Path(assignment.worktree).is_absolute(), 'Worker worktree must be an absolute path')
        path = Path(assignment.worktree).resolve(strict=True)
        wf.require(path != self.root, 'Editing workers require a separate worktree')
        common = Path(git(self.root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        other = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        wf.require(common == other, 'Worker must be a worktree of the authoritative repository')
        wf.require(git(path, 'branch', '--show-current') == assignment.branch, 'Assigned branch is not checked out')
        wf.require(git(self.root, 'branch', '--show-current') != assignment.branch, 'Dedicated worker branch required')
        return path

    def validate_assignment(self, assignment):
        _, _, task = self.task(assignment.task_id)
        wf.require(task['status'] == 'planned', 'Assign a planned task')
        wf.require(set(assignment.paths) == set(task['paths']), 'Assignment paths must match the approved task scope')
        wf.require(assignment.acceptance_criteria == task['criteria'], 'Acceptance criteria must match the workflow')
        wf.require(not task['claim'] or not coord.live_claim(task), 'Task already claimed')
        path = self.location(assignment)
        head = git(path, 'rev-parse', '--verify', 'HEAD')
        canonical_stamp = self.stamp(assignment.task_id)
        _, config, _ = self.task(assignment.task_id)
        previous_root = wf.ROOT
        try:
            wf.ROOT = path
            worker_source = self.snapshot(config, task)['hash']
        finally:
            wf.ROOT = previous_root
        return hashlib.sha256(json.dumps({'canonical': canonical_stamp, 'worker_head': head,
                                          'worker_source': worker_source}, sort_keys=True).encode()).hexdigest()

    def start(self, assignment, operation_id, stamp):
        _, _, task = self.task(assignment.task_id)
        receipt = task.get('coordination_start')
        if receipt == {'operation_id': operation_id, 'agent': assignment.recipient}:
            return  # Workflow commit succeeded before service receipt; never run it again.
        wf.require(self.validate_assignment(assignment) == stamp, 'Approval is stale; obtain local reapproval')
        wf.AGENT = assignment.recipient
        args = wf.parser().parse_args(['start', assignment.task_id, '--operation-id', operation_id])
        with contextlib.redirect_stdout(io.StringIO()):
            result = wf.dispatch(args)  # Already holding the canonical workflow lock.
        _, _, current = self.task(assignment.task_id)
        wf.require(current.get('coordination_start') == {'operation_id': operation_id, 'agent': assignment.recipient},
                   'Workflow did not commit the assignment start')
        wf.require(result in (None, 0, 2), 'Workflow start failed')

    def status(self, task_id):
        _, _, task = self.task(task_id)
        return {name: task[name] for name in ('id', 'title', 'status', 'paths', 'criteria')}
