"""North Star checkpoints. Python tracks gates; contributors supply the synthesis."""
import hashlib
import json
import subprocess

RECORD = '.workflow/north-star.json'
DRAFT = '.workflow/NORTH_STAR.draft.md'
APPROVAL = '.workflow/north-star-approval.json'
SECTIONS = ('## Core vision', '## Guiding principles', '## Worth building', '## Exclusions', '## Drift assessment')


def git(wf, *args):
    result = subprocess.run(['git', *args], cwd=wf.ROOT, capture_output=True)
    wf.require(result.returncode == 0, result.stderr.decode('utf-8', 'replace'))
    return result.stdout


def load(wf):
    path = wf.local(RECORD)
    return wf.read(path) if path.exists() else {}


def committed(wf, record):
    if not record.get('approval'):
        return False
    try:
        for ref, content in ((APPROVAL, record['approval']), ('NORTH_STAR.md', record['content'])):
            expected = (wf.encoded(content) + '\n').encode() if isinstance(content, dict) else content.encode()
            if git(wf, 'show', 'HEAD:' + ref) != expected or wf.local(ref).read_bytes() != expected:
                return False
        return True
    except (ValueError, OSError):
        return False


def status(wf, state):
    record = load(wf)
    published = committed(wf, record)
    completed = [t['id'] for t in state['tasks'] if t['status'] == 'done']
    covered = record.get('approval', {}) if published else {}
    reasons = []
    if len(completed) // 10 > covered.get('completed_count', 0) // 10:
        reasons.append('ten-task checkpoint')
    assessed = covered.get('significant_tasks', [])
    significant = [t['id'] for t in state['tasks'] if t['status'] == 'done' and t.get('significant', False)]
    if set(significant) - set(assessed):
        reasons.append('significant feature completed')
    if wf.local('NORTH_STAR.md').exists() and not published:
        reasons.append('unapproved or changed NORTH_STAR.md detected')
    if record.get('draft_pending') and (not published or record.get('preview_hash') != covered.get('preview_hash')):
        reasons.append('draft awaiting approval and commit')
    return dict(pending=bool(reasons), reasons=reasons, completed_count=len(completed),
                significant_tasks=significant, authoritative=published,
                reference='NORTH_STAR.md' if published else 'PROJECT.md')


def token(wf, state, content, previous):
    # Bind approval to current repository sources, configuration and task identities/statuses.
    config = wf.read(wf.local('.workflow/config.json'))
    snapshot = wf.fingerprint(config, dict(source_files=[], criteria=[], data_mode='none', analysis_phases=[]))
    snapshot['files'].pop('NORTH_STAR.md', None)
    # Cover repository documentation and untracked source evidence too.
    try:
        names = git(wf, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')
    except ValueError:
        names = []
    for name in names:
        if name and name != 'NORTH_STAR.md' and wf.source_allowed(name) and not name.startswith('.claude/agents/'):
            path = wf.local(name)
            snapshot['files'][name] = wf.digest(path.read_bytes()) if path.is_file() else 'deleted'
    payload = dict(sources=snapshot['files'], config=config, tasks=state['tasks'], content=content, previous=previous)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def dispatch(wf, args, state):
    info = status(wf, state)
    record = load(wf)
    if args.action == 'status':
        print(wf.encoded(info)); return
    if args.action in ('assess', 'draft'):
        wf.require(args.file, '--file required')
    if args.action == 'assess':
        task = next((t for t in state['tasks'] if t['id'] == args.task), None)
        wf.require(task and task['status'] == 'planned', 'Assess a planned task before implementation')
        wf.require_access(task, 'north-star assess')
        report = wf.body(args.file)
        wf.require(bool(report.strip()), 'Alignment assessment required')
        task['north_star_alignment'] = dict(reference=info['reference'], report=report,
                                             reference_hash=wf.digest(wf.local(info['reference']).read_bytes()))
        wf.save(state); return
    if args.action == 'draft':
        content = wf.body(args.file)
        wf.require(all(s in content for s in SECTIONS), 'Draft requires sections: ' + ', '.join(SECTIONS))
        wf.require('## Repository evidence' in content, 'Cite repository paths and distinguish facts, inference and unknowns')
        wf.write(wf.local(DRAFT), content)
        previous = wf.local('NORTH_STAR.md').read_text(encoding='utf-8') if wf.local('NORTH_STAR.md').exists() else None
        record.update(draft_pending=True, publishing=False, previous=previous, preview_hash=token(wf, state, content, previous))
        wf.dump(wf.local(RECORD), record)
        print(wf.encoded(dict(preview_hash=record['preview_hash'], draft=DRAFT, checkpoint=info))); return
    wf.require(args.approved and args.preview_hash, 'Explicit user approval and --preview-hash required')
    # A commit that succeeded before an interruption is recoverable without another commit.
    if committed(wf, record) and record.get('approval', {}).get('preview_hash') == args.preview_hash:
        print('North Star publication already committed.'); return
    content = wf.local(DRAFT).read_text(encoding='utf-8')
    current = wf.local('NORTH_STAR.md').read_text(encoding='utf-8') if wf.local('NORTH_STAR.md').exists() else None
    previous = record.get('previous') if record.get('publishing') and current == record.get('content') else current
    wf.require(record.get('preview_hash') == args.preview_hash == token(wf, state, content, previous),
               'Stale approval; regenerate and approve the draft')
    git(wf, 'rev-parse', '--show-toplevel')
    approval = dict(preview_hash=args.preview_hash, completed_count=info['completed_count'],
                    significant_tasks=info['significant_tasks'], agent=wf.AGENT)
    wf.write(wf.local('NORTH_STAR.md'), content)
    wf.dump(wf.local(APPROVAL), approval)
    record.update(approval=approval, content=content, draft_pending=True, publishing=True)
    wf.dump(wf.local(RECORD), record)
    # --only excludes unrelated staged work; normal commit hooks always run.
    git(wf, 'add', '--', 'NORTH_STAR.md', APPROVAL, RECORD)
    git(wf, 'commit', '--only', '-m', 'Publish approved North Star assessment', '--', 'NORTH_STAR.md', APPROVAL, RECORD)
    print('Approved North Star committed; new task starts may resume.')


def require_start(wf, state, task):
    info = status(wf, state)
    wf.require(not info['pending'], 'North Star review pending; new starts/claims blocked: ' + ', '.join(info['reasons']))
    if task.get('significant', False):
        assessment = task.get('north_star_alignment', {})
        wf.require(assessment.get('reference') == info['reference'] and
                   assessment.get('reference_hash') == wf.digest(wf.local(info['reference']).read_bytes()),
                   'Significant feature needs current alignment assessment: north-star assess --task TASK --file REPORT')


def notify(wf, state):
    info = status(wf, state)
    return ('North Star review pending: ' + ', '.join(info['reasons']) +
            '. New starts/claims blocked. Synthesize repository evidence into a draft; obtain approval before publishing.'
            if info['pending'] else 'North Star reference: ' + info['reference'])


def checkpoint(wf, state):
    if status(wf, state)['pending'] and not wf.local(DRAFT).exists():
        wf.write(wf.local(DRAFT), '# Pending North Star synthesis\n\n'
                 'UNAPPROVED: summarize the current repository before requesting publication.\n\n' +
                 '\n\n'.join(SECTIONS + ('## Repository evidence',)) + '\n')
