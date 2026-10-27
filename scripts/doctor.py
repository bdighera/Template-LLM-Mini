"""Read-only setup checks (`workflow.py doctor`): Git hooks and identity, agent
identity, template remotes, and approved fixtures. Each finding is ok, warn, or note."""
import os
from pathlib import Path
import subprocess

TEMPLATE_MARKER = '# Configure this template first'


def git(root, *args):
    try:
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace')
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def unconfigured_template(location, root):
    path = Path(location) if os.path.isabs(location) else root / location
    agents = path / 'AGENTS.md'
    return (path.is_dir() and not (path / '.workflow/config.json').exists() and agents.is_file()
            and agents.read_text(encoding='utf-8', errors='replace').startswith(TEMPLATE_MARKER))


def checks(root, config, agent_from_env):
    found = []
    add = lambda level, message: found.append((level, message))
    if agent_from_env:
        add('ok', f'agent identity from WORKFLOW_AGENT ({os.environ["WORKFLOW_AGENT"]})')
    else:
        add('warn', 'WORKFLOW_AGENT is unset, so work is recorded as "local"; Claude gets it from '
                    '.claude/settings.json when hooks load, other agents export it (e.g. WORKFLOW_AGENT=codex)')
    session_root = os.environ.get('CLAUDE_PROJECT_DIR')
    if session_root and Path(session_root).resolve() != root.resolve():
        add('warn', f'Claude Code session root is {session_root}; this project\'s hooks are not loaded. '
                    f'Start Claude Code in {root}, or call workflow.py guard before writes')
    elif not session_root:
        add('note', f'Claude hooks load only for sessions started in {root}')
    if git(root, 'rev-parse', '--is-inside-work-tree') != 'true':
        add('warn', 'not a Git work tree: run `git init`, then `workflow.py install-hooks`')
        return found
    hooks = git(root, 'config', '--get', 'core.hooksPath')
    add(*(('ok', 'core.hooksPath=githooks') if hooks == 'githooks'
          else ('warn', f'core.hooksPath is {hooks or "unset"}; run `workflow.py install-hooks`')))
    if not os.access(root / 'githooks/pre-commit', os.X_OK):
        add('warn', 'githooks/pre-commit is not executable; run `workflow.py install-hooks`')
    name, email = git(root, 'config', 'user.name'), git(root, 'config', 'user.email')
    add(*(('ok', f'git identity {name} <{email}>') if name and email
          else ('warn', 'git user.name/user.email unset; commits will fail (git config user.name/user.email)')))
    for remote in (git(root, 'remote') or '').split():
        url = git(root, 'remote', 'get-url', remote) or ''
        if remote != 'template' and unconfigured_template(url, root):
            add('warn', f'remote {remote} points at the template ({url}); '
                        f'run `git remote rename {remote} template` so nothing is pushed to it')
    if config:
        for ref in config['data']['approved_fixtures']:
            if not (root / ref).is_file():
                add('warn', f'approved fixture missing: {ref}')
            elif git(root, 'check-ignore', '-q', ref) is not None:
                add('warn', f'approved fixture is ignored by Git: {ref}; run `workflow.py install-hooks`')
    return found


def render(found):
    return '\n'.join(f'{level}: {message}' for level, message in found)
