"""Mechanical rule enforcement shared by Claude Code hooks, the Git pre-commit hook,
and manual or Codex calls (`workflow.py guard edit|bash|commit|session`).

Judgment rules stay in AGENTS.md; only rules Python can check reliably live here.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import coordination as coord
import doctor
import roles
import workflow as wf

# Written only by workflow.py (or sync-agents); hand edits break evidence hashes.
PROTECTED = ('.workflow', 'handoffs', 'prompts/logs', 'memories/features', 'memories/index.md', 'tasks.md',
             'logging/journal.jsonl', 'logging/journal.md', '.claude/agents')
RAW = 'data/raw'
ALLOW_SECRET = 'guard: allow-secret'
SECRETS = [
    ('AWS access key', re.compile(r'AKIA[0-9A-Z]{16}')),
    ('private key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('GitHub token', re.compile(r'\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})')),
    ('Anthropic key', re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}')),
    ('API secret key', re.compile(r'\bsk-[A-Za-z0-9]{32,}')),
    ('Slack token', re.compile(r'\bxox[abposr]-[A-Za-z0-9-]{10,}')),
    ('credential assignment', re.compile(r'(?i)\b(api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*["\'][^"\'\s]{16,}["\']')),
]
WRITE_COMMANDS = re.compile(r'\b(rm|mv|cp|tee|truncate|dd|ln|chmod|unlink|shred)\b|\bsed\s+(-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-zA-Z]*i')
REDIRECT = re.compile(r'>{1,2}\s*([^\s;&|<>]+)')
DOWNLOAD = re.compile(r'\b(curl|wget|aria2c|Invoke-WebRequest|iwr)\b')
HOOK_BYPASS = re.compile(r'\bgit\b[^;&|]*\bcommit\b[^;&|]*(--no-verify|\s-[a-zA-Z]*n\b)|(?i:core\.hooksPath)')  # Git config keys are case-insensitive.


def relative(path):
    if not path:
        return None
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else wf.ROOT / candidate
    try:
        return candidate.resolve().relative_to(wf.ROOT.resolve()).as_posix()
    except ValueError:
        return None


def under(rel, prefix):
    return rel == prefix or rel.startswith(prefix + '/')


def protected(rel):
    return next((prefix for prefix in PROTECTED if under(rel, prefix)), None)


def secrets(content):
    return sorted({name for line in content.splitlines() if ALLOW_SECRET not in line
                   for name, pattern in SECRETS if pattern.search(line)})


def project():
    if not (wf.ROOT / '.workflow/config.json').exists():
        return None, None
    return wf.read(wf.ROOT / '.workflow/config.json'), wf.read(wf.ROOT / '.workflow/state.json')


def edit_decision(path, agent, role=None, content=''):
    """Return None to allow, or (deny|ask, reason)."""
    rel = relative(path)
    if rel is None:
        return None
    hit = protected(rel)
    if hit:
        fix = 'run `workflow.py sync-agents` after editing agents/<role>.md' if hit == '.claude/agents' else 'use the matching workflow.py command'
        return 'deny', f'{rel} is written only by scripts/workflow.py ({hit}); {fix}.'
    if under(rel, RAW):
        if (wf.ROOT / rel).exists():
            return 'deny', f'{rel} is a read-only original; write transformations to data/processed/.'
        return 'ask', f'New file in {RAW}/: staging external data needs explicit user approval.'
    config, state = project()
    if config:
        if role:
            definitions = roles.configured(wf.ROOT, config)
            if role in definitions:
                writes = definitions[role]['writes']
                explicit = [pattern for pattern in writes if pattern != 'task']
                if 'task' not in writes and not coord.covers(explicit, rel):
                    return 'deny', f'The {role} role may not write {rel} (writes: {writes}); return findings to the coordinator.'
        if wf.in_sources(config, rel):
            holders = [t for t in state['tasks'] if coord.live_claim(t) and coord.covers(t['paths'], rel)]
            foreign = [t for t in holders if t['claim']['agent'] != agent]
            if foreign:
                t = foreign[0]
                return 'deny', f'{rel} is inside {t["id"]} paths, claimed by {t["claim"]["agent"]} until {t["claim"]["expires"]}.'
            if not holders:
                return 'deny', (f'{rel} is project source but {agent} holds no live claim covering it. '
                                'Register it (`workflow.py add ... --path GLOB`, then `start`), or `claim`/`scope` an existing task.')
    found = secrets(content)
    if found:
        return 'ask', f'Content looks like it contains a secret ({", ".join(found)}); redact it unless the user approves.'
    return None


def bash_decision(command, agent):
    if HOOK_BYPASS.search(command):
        return 'deny', 'Do not bypass or repoint commit hooks; ask the user if a hook blocks you.'
    targets = [relative(target) for target in REDIRECT.findall(command)]
    writes = [t for t in targets if t and (protected(t) or under(t, RAW))]
    if WRITE_COMMANDS.search(command):
        writes += [prefix for prefix in PROTECTED + (RAW,) if prefix in command]
    if writes:
        return 'deny', (f'Command would modify {", ".join(sorted(set(writes)))}: workflow files change only through '
                        'scripts/workflow.py and data/raw/ is read-only.')
    if DOWNLOAD.search(command):
        return 'ask', ('Downloads need explicit user approval; prefer a registered source and '
                       '`workflow.py fetch` (data add-on), and stage external evidence under data/ only.')
    return None


def git(*args):
    return subprocess.run(['git', *args], cwd=wf.ROOT, capture_output=True)


def commit_issues():
    config, _ = project()
    fixtures = set(config['data']['approved_fixtures']) if config else set()
    names = [n for n in git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z').stdout.decode().split('\0') if n]
    deleted = [n for n in git('diff', '--cached', '--name-only', '--diff-filter=D', '-z').stdout.decode().split('\0') if n]
    issues = []
    journal = 'logging/journal.jsonl'
    old = git('show', f'HEAD:{journal}')
    if old.returncode == 0 and (journal in names or journal in deleted):
        new = git('show', f':{journal}')
        if new.returncode or not new.stdout.startswith(old.stdout):
            issues.append(f'{journal} must only grow; append corrections with `workflow.py decide --supersedes`.')
    if config and '.gitignore' in names:
        staged = git('show', ':.gitignore').stdout.decode('utf-8', 'replace')
        if wf.gitignore_with_fixtures(staged, config) != staged:
            issues.append('.gitignore: the approved_fixtures block must match data.approved_fixtures; '
                          'run `workflow.py install-hooks` to regenerate it.')
    for name in names:
        parts = Path(name).parts
        if parts[0] == 'data' and Path(name).name != '.gitkeep' and name not in fixtures:
            issues.append(f'{name}: data contents stay out of Git unless listed in data.approved_fixtures.')
        if any(p == '.env' or (p.startswith('.env.') and p != '.env.example') for p in parts):
            issues.append(f'{name}: environment files must not be committed.')
    current = None
    diff = git('diff', '--cached', '-U0', '--no-color', '--diff-filter=ACMR').stdout.decode('utf-8', 'replace')
    for line in diff.splitlines():
        if line.startswith('+++ '):
            current = line[6:] if line.startswith('+++ b/') else None
        elif line.startswith('+') and current:
            for found in secrets(line[1:]):
                issues.append(f'{current}: possible {found}; redact it (or mark a test fixture line with "{ALLOW_SECRET}").')
    return issues


def session_text(agent, source=None):
    lines = [f'[harness] agent={agent}. Hooks enforce the mechanical rules (workflow files, read-only data/raw, '
             'claims before source edits, downloads, secrets, commit checks); if one blocks you, read its reason '
             'and do not route around it.']
    config, state = project()
    if not config:
        lines.append('This template is unconfigured: read AGENTS.md before doing anything else.')
        return '\n'.join(lines)
    lines.append(f'Project: {config["project"]["name"]} - {config["project"]["purpose"]}')
    explicit = bool(os.environ.get('WORKFLOW_AGENT')) or agent != 'local'
    lines += [f'Setup warning: {message}' for level, message in doctor.checks(wf.ROOT, config, explicit) if level == 'warn']
    active = [t for t in state['tasks'] if t['status'] != 'done']
    lines.append('Board:' if active else 'Board: no active tasks.')
    for t in active:
        claim = coord.live_claim(t)
        owner = f'{claim["agent"]} until {claim["expires"]}' if claim else 'unclaimed'
        lines.append(f'- {t["id"]} {t["status"]} [{owner}] paths={",".join(t["paths"])}: {t["title"]}')
    mine = [t for t in active if coord.live_claim(t) and t['claim']['agent'] == agent]
    expired = [t for t in active if t.get('claim') and t['claim']['agent'] == agent and not coord.live_claim(t)]
    if expired:
        lines.append('Expired leases (heartbeat to renew): ' + ', '.join(t['id'] for t in expired))
    if mine:
        lines.append(f'Your claims: {", ".join(t["id"] for t in mine)}. Next: workflow.py context {mine[0]["id"]} --role <role>')
        if source in ('compact', 'resume'):
            try:
                lines += ['Resume packet:', wf.context(mine[0], config, 'coordinator', min(config['context']['max_chars'], 6000))]
            except (ValueError, OSError) as exc:
                lines.append(f'Resume packet unavailable ({exc}); run workflow.py resume {mine[0]["id"]}.')
    return '\n'.join(lines)


def hook(agent, role):
    payload = json.load(sys.stdin)
    if payload.get('hook_event_name') == 'SessionStart':
        print(session_text(agent, payload.get('source')))
        return 0
    tool, data = payload.get('tool_name', ''), payload.get('tool_input') or {}
    role = role or payload.get('agent_type')
    try:
        if tool in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
            content = (data.get('content') or data.get('new_string') or data.get('new_source')
                       or ''.join(edit.get('new_string', '') for edit in data.get('edits', [])))
            decision = edit_decision(data.get('file_path') or data.get('notebook_path'), agent, role, content)
        elif tool == 'Bash':
            decision = bash_decision(data.get('command', ''), agent)
        else:
            decision = None
    except Exception as exc:  # Fail closed: a broken guard must not silently allow.
        decision = ('deny', f'guard error ({type(exc).__name__}: {exc}); run `workflow.py check` or ask the user.')
    if decision:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': decision[0],
                                                 'permissionDecisionReason': decision[1]}}))
    return 0


def report(decision):
    print(json.dumps({'decision': decision[0] if decision else 'allow', 'reason': decision[1] if decision else ''}))
    return 2 if decision else 0


def run(args, agent):
    role = args.role or os.environ.get('WORKFLOW_ROLE')
    if args.mode == 'hook':
        return hook(agent, role)
    if args.mode == 'session':
        source = None
        if not sys.stdin.isatty():
            raw = sys.stdin.read().strip()
            source = json.loads(raw).get('source') if raw else None
        print(session_text(agent, source))
        return 0
    if args.mode == 'edit':
        wf.require(args.path, 'guard edit needs --path')
        content = Path(args.content_file).read_text(encoding='utf-8') if args.content_file else ''
        return report(edit_decision(args.path, agent, role, content))
    if args.mode == 'bash':
        wf.require(args.shell_command, 'guard bash needs --command')
        return report(bash_decision(args.shell_command, agent))
    issues = commit_issues()
    for issue in issues:
        print(f'pre-commit: {issue}', file=sys.stderr)
    return 1 if issues else 0
