"""Cross-agent coordination: a state write lock, task path scopes, and claim leases."""
import contextlib
from datetime import datetime, timedelta, timezone
import os
from pathlib import PurePosixPath
import re
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

AGENT_PATTERN = re.compile(r'[a-z][a-z0-9_-]{0,31}')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def current_agent(explicit=None):
    agent = explicit or os.environ.get('WORKFLOW_AGENT') or 'local'
    require(AGENT_PATTERN.fullmatch(agent), f'Invalid agent name: {agent!r} (use lowercase letters, digits, - or _)')
    return agent


@contextlib.contextmanager
def locked(root):
    """Serialize every state/journal mutation across processes and agents."""
    path = root / '.workflow/lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + float(os.environ.get('WORKFLOW_LOCK_TIMEOUT', '300'))
    with path.open('a+') as handle:
        while True:
            try:
                if fcntl:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                require(time.monotonic() < deadline, 'Another agent holds the workflow lock; retry shortly')
                time.sleep(0.1)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_UN)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def atomic_write(path, content):
    """Replace path with content (LF line endings on every platform) via a temporary file.

    Windows refuses the rename while another process (editor, indexer, antivirus) has
    the target open, so retry briefly before giving up.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(content)
    deadline = time.monotonic() + 5
    while True:
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def normalize_pattern(pattern):
    require(isinstance(pattern, str) and pattern.strip(), 'Task paths must be nonempty strings')
    path = PurePosixPath(pattern.strip().removeprefix('./'))
    require(not path.is_absolute() and '..' not in path.parts and path.parts, f'Task path must stay inside the project: {pattern}')
    return path.as_posix()


def _wild(part):
    return any(char in part for char in '*?[')


def matches(pattern, path):
    """`dir` and `dir/**` cover everything below dir; `*` stays within one segment."""
    if not _wild(pattern):
        return path == pattern or path.startswith(pattern.rstrip('/') + '/')
    regex = ''
    index = 0
    while index < len(pattern):
        if pattern.startswith('**/', index):
            regex += '(?:.*/)?'; index += 3
        elif pattern.startswith('**', index):
            regex += '.*'; index += 2
        elif pattern[index] == '*':
            regex += '[^/]*'; index += 1
        elif pattern[index] == '?':
            regex += '[^/]'; index += 1
        else:
            regex += re.escape(pattern[index]); index += 1
    return re.fullmatch(regex, path) is not None


def covers(patterns, path):
    return any(matches(pattern, path) for pattern in patterns)


def overlaps(first, second):
    """Conservative: scopes overlap when one static prefix contains the other."""
    def prefix(pattern):
        parts = []
        for part in PurePosixPath(pattern).parts:
            if _wild(part):
                break
            parts.append(part)
        return parts
    for a in first:
        for b in second:
            pa, pb = prefix(a), prefix(b)
            shorter = min(len(pa), len(pb))
            if pa[:shorter] == pb[:shorter]:
                return True
    return False


def now():
    return datetime.now(timezone.utc)


def live_claim(task, at=None):
    claim = task.get('claim')
    if not claim or task['status'] == 'done':
        return None
    return claim if datetime.fromisoformat(claim['expires']) > (at or now()) else None


def new_claim(agent, minutes):
    return {'agent': agent, 'expires': (now() + timedelta(minutes=minutes)).isoformat(timespec='seconds')}


def claim_conflicts(state, task, agent, max_concurrency):
    """Return why `agent` cannot claim `task`, or None."""
    current = live_claim(task)
    if current and current['agent'] != agent:
        return f'{task["id"]} is claimed by {current["agent"]} until {current["expires"]}'
    # One agent may hold several (even overlapping) tasks; scopes only conflict across agents.
    others = [t for t in state['tasks'] if t['id'] != task['id'] and live_claim(t) and t['claim']['agent'] != agent]
    for other in others:
        if overlaps(task['paths'], other['paths']):
            return (f'{task["id"]} paths {task["paths"]} overlap {other["id"]} paths {other["paths"]}, '
                    f'claimed by {other["claim"]["agent"]}; narrow the scope or wait')
    agents = {t['claim']['agent'] for t in others}
    if agent not in {t['claim']['agent'] for t in state['tasks'] if live_claim(t)} and len(agents) >= max_concurrency:
        return f'max_concurrency={max_concurrency} agents already hold claims: ' + ', '.join(sorted(agents))
    return None
