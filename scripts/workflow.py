#!/usr/bin/env python3
"""Standard-library workflow controller. Every mutation runs under a cross-process
lock; agents (Claude, Codex, ...) claim tasks with path scopes and leases."""
import argparse
import difflib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import coordination as coord
import decision_journal
import doctor
import roles

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'addons/data_analysis'
VERSION = 4
CONFIG_VERSION = 2
AGENT = 'local'  # Set from --agent or WORKFLOW_AGENT in main().
AGENT_EXPLICIT = False  # False when AGENT fell back to 'local'.
FIELDS = ('objective', 'files', 'memories', 'decisions', 'changes', 'validation',
          'unresolved', 'next_action')
KINDS = ('prompts', 'handoffs', 'reviews', 'tests', 'memories', 'checkpoints', 'inspections', 'analyses', 'profiles', 'fetches')
# Never hash datasets, workflow records, environments, or generated caches.
EXCLUDED = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache',
            '.ruff_cache', 'node_modules'}
ROOT_EXCLUDED = {'data', '.workflow', 'handoffs', 'memories', 'logging', 'dist', 'build'}
# Template components a project may delete once its use case is known (config.prune).
PRUNABLE = {
    'data_analysis': ['addons/data_analysis'],  # Includes the add-on's tests.
    'template_history': ['docs/template-history'],
    'initialization_primer': ['prompts/template_prompt_initialization.md'],
    'configuration_interview': ['CONFIGURE.md'],
    'claude_integration': ['CLAUDE.md', '.claude/settings.json', '.claude/agents'],
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def encoded(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(path)


def dump(path, value):
    write(path, encoded(value) + '\n')


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and all(text(x) for x in value)


def local(ref):
    require(text(ref), 'Expected a project-relative path')
    path = Path(ref)
    require(not path.is_absolute() and '..' not in path.parts, 'Paths must stay inside the project')
    current = ROOT
    for part in path.parts:
        current /= part
        require(not current.is_symlink(), f'Symlinks are not supported: {ref}')
    require(current.resolve().is_relative_to(ROOT.resolve()), 'Path escapes project')
    return current


def source_allowed(ref):
    parts = Path(ref).parts
    return (not any(p in EXCLUDED for p in parts)
            and (not parts or parts[0] not in ROOT_EXCLUDED)
            and not (len(parts) >= 2 and parts[:2] == ('prompts', 'logs'))
            and Path(ref).name not in {'tasks.md', 'project-config.json'}
            and not any(p == '.env' or p.startswith('.env.') for p in parts)
            and Path(ref).suffix not in {'.pyc', '.tmp'})


def data_enabled(config):
    return config['addons']['data_analysis']


def da():
    """The optional data-analysis add-on; imported only when enabled."""
    require((ADDON / 'data_analysis.py').is_file(), 'Data-analysis add-on is not installed (addons/data_analysis)')
    if str(ADDON) not in sys.path:
        sys.path.insert(0, str(ADDON))
    import data_analysis
    return data_analysis


def fetching():
    da()
    import fetching
    return fetching


def source_roots(config):
    return config['validation']['source_paths'] + (['addons/data_analysis'] if data_enabled(config) else [])


def in_sources(config, ref):
    return source_allowed(ref) and coord.covers(source_roots(config), ref)


def role_ref(role):
    return f'agents/{role}.md'


def prune_targets(config):
    """Existing paths that config.prune deletes, including add-on role definitions."""
    refs = [ref for name in config['prune'] for ref in PRUNABLE[name]]
    if 'data_analysis' in config['prune'] and (ROOT / 'agents').is_dir():
        refs += [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / 'agents').glob('*.md'))
                 if roles.parse(path)['requires'] == 'data_analysis']
    return [ref for ref in refs if local(ref).exists()]


def prune(refs):
    for ref in refs:
        path = local(ref)
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        # Drop parents emptied by the removal (e.g. docs/), never the project root.
        parent = path.parent
        while parent != ROOT and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


FIXTURES_BEGIN = '# >>> workflow: approved_fixtures (generated; change via update-config)'
FIXTURES_END = '# <<< workflow'


def gitignore_with_fixtures(content, config):
    """.gitignore text with the generated block that un-ignores data.approved_fixtures."""
    lines, inside = [], False
    for line in content.splitlines():
        if line == FIXTURES_BEGIN:
            inside = True
        elif inside and line == FIXTURES_END:
            inside = False
        elif not inside:
            lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    fixtures = config['data']['approved_fixtures']
    if fixtures:
        lines += ['', FIXTURES_BEGIN, *[f'!/{ref}' for ref in fixtures], FIXTURES_END]
    return '\n'.join(lines) + '\n'


def sync_gitignore(config):
    path = ROOT / '.gitignore'
    current = path.read_text(encoding='utf-8') if path.exists() else ''
    expected = gitignore_with_fixtures(current, config)
    if expected != current:
        write(path, expected)


def claude_enabled(config):
    return 'claude_integration' not in config['prune']


def validate_config(c):
    template = read(ROOT / 'templates/config.json')
    require(isinstance(c, dict) and isinstance(c.get('addons'), dict) and type(c['addons'].get('data_analysis')) is bool,
            'config.addons.data_analysis missing; start from templates/config.json (config_version 2)')
    if c['addons']['data_analysis']:
        require((ADDON / 'config.json').is_file(), 'Data-analysis add-on is not installed (addons/data_analysis)')
        template['analysis'] = read(ADDON / 'config.json')
    empty_ok = ('config.analysis.datasets', 'config.analysis.provenance', 'config.analysis.sources',
                'config.data.approved_fixtures', 'config.prune')

    errors = []

    def structure(actual, expected, location='config'):
        # Collect every structural problem so one validate-config run lists them all.
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                errors.append(f'{location}: expected an object')
                return
            missing, extra = sorted(set(expected) - set(actual)), sorted(set(actual) - set(expected))
            if missing:
                errors.append(f'{location}: missing keys {", ".join(missing)}')
            if extra:
                errors.append(f'{location}: unexpected keys {", ".join(extra)}')
            for key in set(expected) & set(actual):
                structure(actual[key], expected[key], location + '.' + key)
        elif isinstance(expected, str):
            if not text(actual):
                errors.append(f'{location}: provide a nonempty string; use none explicitly')
        elif type(expected) is bool:
            if type(actual) is not bool:
                errors.append(f'{location}: expected boolean')
        elif type(expected) is int:
            if type(actual) is not int:
                errors.append(f'{location}: expected integer')
        elif isinstance(expected, list):
            if not (isinstance(actual, list) and (actual or location in empty_ok)):
                errors.append(f'{location}: expected nonempty list')
    structure(c, template)
    require(not errors, f'Configuration has {len(errors)} problem(s):\n- ' + '\n- '.join(sorted(errors)))
    require(c['config_version'] == CONFIG_VERSION, f'config.config_version: unsupported; expected {CONFIG_VERSION}')
    if data_enabled(c):
        da().validate_settings(c['analysis'], ROOT)
        profile_settings = c['analysis']['profiling']
        require(1 <= profile_settings['max_columns'] <= 1000 and 500 <= profile_settings['max_chars'] <= 100000, 'config.analysis.profiling: max_columns must be 1..1000 and max_chars 500..100000')
        require(not profile_settings['enabled'] or c['analysis']['data_access'] == 'approved-summaries', 'config.analysis.profiling: enabled requires data_access approved-summaries')
        da().validate_provenance(ROOT, c['analysis']['provenance'])
        fetching().validate_sources(c['analysis']['sources'], ROOT)
    require(strings(c['prune']) and len(set(c['prune'])) == len(c['prune']) and set(c['prune']) <= set(PRUNABLE),
            f'config.prune: entries must be unique names from: {", ".join(PRUNABLE)}')
    require(not ('data_analysis' in c['prune'] and data_enabled(c)), 'config.prune: cannot prune data_analysis while addons.data_analysis is true')
    require(strings(c['data']['approved_fixtures']), 'config.data.approved_fixtures: must be data/ paths')
    for ref in c['data']['approved_fixtures']:
        local(ref)
        require(Path(ref).parts[0] == 'data', f'config.data.approved_fixtures: must be under data/: {ref}')
    require(strings(c['project']['acceptance_criteria']), 'config.project.acceptance_criteria: must be strings')
    require(strings(c['roles']) and len(set(c['roles'])) == len(c['roles']), 'config.roles: must be unique names')
    require(set(roles.REQUIRED_ROLES) <= set(c['roles']), 'config.roles: required roles are builder, reviewer, tester')
    require('coordinator' not in c['roles'], 'config.roles: coordinator is implicit; do not list it')
    roles.configured(ROOT, c)
    require(c['delegation']['review_mode'] in ('independent', 'approved-same-agent', 'cross-agent'), 'config.delegation.review_mode: use independent, approved-same-agent, or cross-agent')
    require(c['delegation']['max_concurrency'] >= 1 and c['handoffs']['max_repairs'] >= 0, 'config.delegation.max_concurrency must be >= 1 and config.handoffs.max_repairs >= 0')
    require(1 <= c['coordination']['lease_minutes'] <= 10080, 'config.coordination.lease_minutes: must be 1..10080')
    for key in ('raw_policy', 'git_policy'):
        require(c['data'][key] == template['data'][key], f'config.data.{key}: keep the template text')
    v = c['validation']
    require(all(strings(command) and command for command in v['commands']),
            'config.validation.commands: each command is a nonempty argument array, '
            'e.g. [["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]]')
    require(1 <= v['timeout_seconds'] <= 3600, 'config.validation.timeout_seconds: must be 1..3600')
    require(v['diagnostics'] in ('off', 'summary'), 'config.validation.diagnostics: must be off or summary')
    require(1 <= v['summary_max_chars'] <= 10000, 'config.validation.summary_max_chars: must be 1..10000')
    require(strings(v['source_paths']), 'config.validation.source_paths: must be strings')
    for ref in v['source_paths']:
        local(ref)
        require(source_allowed(ref), f'config.validation.source_paths: cannot fingerprint generated/data/environment path {ref}')
    require(2000 <= c['context']['max_chars'] <= 100000, 'config.context.max_chars: must be 2000..100000')


def body(path):
    value = Path(path).read_text(encoding='utf-8').strip()
    require(value, 'Artifact cannot be empty')
    return value + '\n'


def artifact(task, kind, content, suffix='json'):
    folder = {'prompts': 'prompts/logs', 'memories': 'memories/features'}.get(kind, 'handoffs')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    ref = f'{folder}/{task["id"]}-{kind}-{stamp}.{suffix}'
    path = local(ref)
    write(path, content)
    task.setdefault(kind, []).append(ref)
    task['artifacts'][ref] = digest(path.read_bytes())
    return ref


def evidence(task, kind):
    require(task[kind], f'Missing {kind} evidence')
    return read(local(task[kind][-1]))



def inspect_phase(task, config, phase):
    current = da().scan(ROOT, config, task, phase)
    entry = task['analysis_state'].get(phase, {})
    if task['data_mode'] == 'none' or not entry.get('inspection'):
        return current
    old = read(local(entry['inspection']))['inspection']
    old_refs = {item['path'] for item in old['files']}
    now_refs = {item['path'] for item in current['files']}
    vanished = sorted(old_refs - now_refs)
    if vanished:
        current.pop('hash', None)
        current['status'] = 'needs-input'
        current['reason'] = ''
        current['issues'].append('Previously inspected data disappeared: ' + ', '.join(vanished))
        current['files'].extend({'path': ref, 'status': 'missing previously used data'} for ref in vanished)
        current['question'] = ('Previously used data is missing: ' + ', '.join(vanished)
                               + '. Please restore it or explicitly approve a data-scope change. How should dependent work proceed?')
        return da().seal(current)
    return current


def analysis_stale(task, config):
    if task['status'] == 'done':
        return False
    return any(entry.get('inspection') and entry.get('data_hash') != inspect_phase(task, config, phase)['hash']
               for phase, entry in task['analysis_state'].items())


def fingerprint(config, task):
    files = set()
    for ref in source_roots(config) + task['source_files']:
        path = local(ref)
        require(path.exists(), f'Fingerprint path missing: {ref}')
        candidates = [path]
        if path.is_dir():
            candidates = []
            for directory, dirs, names in os.walk(path, followlinks=False):
                dirs[:] = [name for name in dirs if source_allowed(
                    (Path(directory) / name).relative_to(ROOT).as_posix())]
                for name in dirs:
                    local((Path(directory) / name).relative_to(ROOT).as_posix())
                candidates.extend(Path(directory) / name for name in names)
        for candidate in candidates:
            name = candidate.relative_to(ROOT).as_posix()
            if not source_allowed(name):
                continue
            candidate = local(name)
            if candidate.is_file():
                files.add(name)
    require(files, 'No source files selected for validation')
    hashes = {ref: digest(local(ref).read_bytes()) for ref in sorted(files)}
    snapshot = {'files': hashes, 'config': config, 'criteria': task['criteria'],
                'data': {phase: inspect_phase(task, config, phase)['hash']
                         for phase in task['analysis_phases']} if task['data_mode'] != 'none' else {}}
    return {'hash': digest(json.dumps(snapshot, sort_keys=True).encode()), 'files': hashes, 'data': snapshot['data']}


def invalidate(task, config, repair=False):
    task['cycle'] += 1
    task['repairs'] += int(repair)
    task['review_pass'] = task['test_pass'] = False
    task['reviewed_by'] = None
    task['snapshot'] = None
    task.pop('previous_status', None)
    task['status'] = 'blocked' if task['repairs'] > config['handoffs']['max_repairs'] else 'building'


def fresh(task, config):
    try:
        return task['snapshot'] is not None and fingerprint(config, task) == task['snapshot']
    except (ValueError, OSError):
        return False


def ensure_fresh(task, config, state):
    if not fresh(task, config):
        invalidate(task, config)
        save(state)
        raise ValueError('Source/configuration changed; passes invalidated. Build and obtain a fresh review.')


def validate_task(task, config, complete=False, live=False):
    for kind in KINDS:
        for ref in task.get(kind, []):
            path = local(ref)
            require(path.is_file() and path.stat().st_size, f'Missing artifact: {ref}')
            require(digest(path.read_bytes()) == task['artifacts'].get(ref), f'Artifact changed: {ref}')
    require(task['prompts'], 'Missing major prompt')
    if complete:
        for phase in task['analysis_phases']:
            entry = task['analysis_state'].get(phase, {})
            require(entry.get('status') in ('pass', 'skipped'), f'{phase} analysis is incomplete')
            if task['data_mode'] != 'none':
                require(entry['data_hash'] == task['snapshot']['data'][phase], 'Analysis selection changed')
            if entry['status'] == 'pass':
                report = read(local(entry['report']))
                require(report['verdict'] == 'pass' and report['data_hash'] == entry['data_hash'], 'Stale analysis report')
        require(task['review_pass'] and task['test_pass'] and task['snapshot'], 'Passing review/tests required')
        for kind in ('reviews', 'tests', 'memories', 'handoffs'):
            item = evidence(task, kind)
            require(item.get('task') == task['id'] and item.get('cycle') == task['cycle']
                    and item.get('snapshot') == task['snapshot']['hash'], f'Stale {kind} evidence')
        require(evidence(task, 'reviews')['verdict'] == 'pass', 'Review rejected')
        results = evidence(task, 'tests')['results']
        require(results and all(r['exit_code'] == 0 for r in results), 'Checks failed')
        require(evidence(task, 'tests')['sources_unchanged'], 'Checks changed their source inputs')
        require(evidence(task, 'handoffs')['target'] == 'coordinator', 'Final tester handoff required')
        if live:
            require(fresh(task, config), 'Evidence no longer matches working files')


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def save(state):
    dump(ROOT / '.workflow/state.json', state)
    lines = ['# Task board', '', 'Generated by scripts/workflow.py; never edit. Claim before editing a task\'s paths;',
             'expired leases may be taken over. Use `workflow.py status` or `show` for focused reads.', '',
             '| Task | Status | Owner | Lease until (UTC) | Paths | Depends | Title |', '|---|---|---|---|---|---|---|']
    for task in state['tasks']:
        claim = coord.live_claim(task)
        lines.append('| ' + ' | '.join(cell(value) for value in (
            task['id'], task['status'], claim['agent'] if claim else '-', claim['expires'] if claim else '-',
            ', '.join(task['paths']), ', '.join(task['dependencies']) or '-', task['title'])) + ' |')
    if not state['tasks']:
        lines.append('| - | - | - | - | - | - | No tasks yet |')
    for task in state['tasks']:
        lines += ['', f'## {task["id"]}: {cell(task["title"])}', '',
                  f'Status: {task["status"]} | Repairs: {task["repairs"]} | Built by: {task.get("built_by") or "-"}'
                  f' | Reviewed by: {task.get("reviewed_by") or "-"}'
                  + (f' | Follows: {task["follows"]}' if task.get('follows') else ''), '',
                  *[f'- {criterion}' for criterion in task['criteria']]]
        if task['analysis_state'] or task.get('data_question'):
            lines += ['', 'Analysis: ' + json.dumps({phase: entry['status'] for phase, entry in task['analysis_state'].items()}),
                      '', 'Data question: ' + (task.get('data_question') or 'none')]
        latest = [f'{kind}: [{Path(task[kind][-1]).name}]({task[kind][-1]})' for kind in KINDS if task.get(kind)]
        lines += ['', 'Latest evidence: ' + ('; '.join(latest) or 'none')]
    write(ROOT / 'tasks.md', '\n'.join(lines) + '\n')
    entries = ['# Feature memories', '', 'Generated; load only relevant entries (`workflow.py recall "<words>"` ranks them).', '']
    for task in state['tasks']:
        for ref in task['memories']:
            entries.append(f'- {task["id"]} {task["title"]}: [{Path(ref).name}](../{ref})')
    if len(entries) == 4:
        entries.append('No project memories yet.')
    write(ROOT / 'memories/index.md', '\n'.join(entries) + '\n')


def recall(state, query, limit):
    """Feature memories ranked by how often they use the query's words; title matches count double."""
    words = set(re.findall(r'\w+', query.lower()))
    require(words and limit > 0, 'recall needs query words and a positive --limit')
    hits = []
    for task in state['tasks']:
        title = sum(w in words for w in re.findall(r'\w+', task['title'].lower()))
        for ref in task['memories']:
            report = read(local(ref)).get('report', '')
            score = 2 * title + sum(w in words for w in re.findall(r'\w+', report.lower()))
            if score:
                hits.append(dict(task=task['id'], title=task['title'], memory=ref, score=score))
    return sorted(hits, key=lambda hit: -hit['score'])[:limit]  # ponytail: keyword scan of every memory; index it if this gets slow


def envelope(task, **values):
    return dict(task=task['id'], cycle=task['cycle'], snapshot=task['snapshot']['hash'] if task['snapshot'] else None,
                agent=AGENT, **values)


def handoff_input(path):
    return validate_handoff(read(path))


def handoff_payload(task, args):
    """Handoff fields from --file, or the previous handoff, with --set overrides."""
    if args.file:
        h = read(args.file)
    else:
        require(task['handoffs'], 'The first handoff needs --file (templates/handoff.json fields)')
        previous = evidence(task, 'handoffs')
        h = {key: previous[key] for key in FIELDS}
        print(f'Reusing handoff fields from {task["handoffs"][-1]}', file=sys.stderr)
    require(isinstance(h, dict), 'Use templates/handoff.json fields')
    for item in args.set:
        key, separator, value = item.partition('=')
        require(separator and key in FIELDS, f'--set takes FIELD=VALUE with FIELD in: {", ".join(FIELDS)}')
        h[key] = [ref.strip() for ref in value.split(',') if ref.strip()] if key in ('files', 'memories') else value
    if args.to == 'coordinator' and task['memories'] and isinstance(h.get('memories'), list):
        if task['memories'][-1] not in h['memories']:
            h['memories'] = h['memories'] + [task['memories'][-1]]
    return validate_handoff(h)


def review_ref(task):
    """Token binding a review report to the reviewed snapshot and cycle."""
    return digest(f'{task["snapshot"]["hash"]}:{task["cycle"]}'.encode())[:12] if task['snapshot'] else None


def validate_handoff(h):
    require(isinstance(h, dict) and set(h) == set(FIELDS), 'Use templates/handoff.json fields')
    for key in FIELDS:
        require(strings(h[key]) if key in ('files', 'memories') else text(h[key]), f'Invalid handoff field: {key}')
    for ref in h['files'] + h['memories']:
        require(local(ref).is_file(), f'Handoff reference missing: {ref} (files and memories must be existing '
                'project-relative file paths, e.g. memories/features/T001-memories-<stamp>.json from workflow.py show)')
    return h


def assignment(task, target, ref):
    instruction = (f'Read AGENTS.md and {role_ref(target)}; read handoff {ref}; '
                   'return results to the coordinator without writing workflow state.'
                   if target != 'coordinator' else
                   f'Read AGENTS.md and final handoff {ref}; verify evidence and close the task.')
    artifact(task, 'prompts', instruction + '\n', 'md')



def analysis_gate(task, config, state, phase):
    require(phase in task['analysis_phases'], 'Phase is not selected for this task')
    inspection = inspect_phase(task, config, phase)
    previous = task['analysis_state'].get(phase, {})
    if previous.get('status') in ('pass', 'skipped') and previous.get('data_hash') == inspection['hash']:
        return True
    pending = task.get('analysis_pending')
    require(not pending or pending['phase'] == phase, 'Resolve the pending analysis phase first')
    resume = pending['resume_status'] if pending else task['status']
    if task['snapshot'] and previous.get('data_hash') != inspection['hash']:
        invalidate(task, config)
        resume = 'building'
    if inspection['status'] == 'skipped':
        task['analysis_state'][phase] = dict(status='skipped', data_hash=inspection['hash'], reason=inspection['reason'])
        if pending:
            task['status'] = resume
            task['analysis_pending'] = None
            task['data_question'] = None
            if resume == 'building':
                assignment(task, 'builder', task['prompts'][0])
        save(state)
        return True
    if previous.get('response_required') and previous.get('data_hash') == inspection['hash']:
        task['status'] = 'awaiting-data'
        print('User question: ' + task['data_question'])
        save(state)
        return False
    if pending and previous.get('status') == 'pending' and previous.get('data_hash') == inspection['hash']:
        print('Analyzer assignment is already pending; read its current context.')
        return False
    ref = artifact(task, 'inspections', encoded(envelope(task, inspection=inspection)) + '\n')
    task['analysis_pending'] = dict(phase=phase, resume_status=resume)
    task['analysis_state'][phase] = dict(status='pending', inspection=ref, data_hash=inspection['hash'])
    task['data_question'] = inspection['question']
    if inspection['status'] == 'needs-input':
        task['status'] = 'awaiting-data'
        task['analysis_state'][phase]['status'] = 'needs-input'
        print('User question: ' + inspection['question'])
    else:
        task['status'] = 'analyzing'
        assignment(task, 'analyzer', ref)
        print(f'{phase} analysis required; give the analyzer its focused context and record analysis-report. '
              f'A passing report resumes {task["id"]} automatically; do not run start again.')
    save(state)
    return False


def analysis_report(task, config, state, args):
    require(task['status'] == 'analyzing' and task['analysis_pending']['phase'] == args.phase,
            'A matching analyzer assignment is required')
    previous = task['analysis_state'][args.phase]
    current = inspect_phase(task, config, args.phase)
    if current['hash'] != previous['data_hash']:
        analysis_gate(task, config, state, args.phase)
        raise ValueError('Data changed during analysis; use the new inspection')
    require(current['status'] == 'ready', 'Data must pass deterministic inspection first')
    report = body(args.file)
    require(args.verdict == 'pass' or text(args.question), 'Explain the discrepancy as a user question')
    ref = artifact(task, 'analyses', encoded(envelope(task, phase=args.phase, data_hash=current['hash'],
                   verdict=args.verdict, report=report, question=args.question)) + '\n')
    previous.update(report=ref, status='pass' if args.verdict == 'pass' else 'needs-input')
    if args.verdict != 'pass':
        task['status'] = 'awaiting-data'
        task['data_question'] = args.question
        previous['response_required'] = True
        print('User question: ' + args.question)
    else:
        resume = task['analysis_pending']['resume_status']
        task['status'] = resume
        task['analysis_pending'] = None
        task['data_question'] = None
        if resume == 'building':
            assignment(task, 'builder', ref)
        print(f'{task["id"]} resumed: {resume} (claim: {task["claim"]["agent"] if task.get("claim") else "none"})')


def run_checks(config):
    v = config['validation']
    results = []
    for command in v['commands']:
        result = {'command': command}
        # Off mode never captures output. Summary mode trusts an approved wrapper,
        # and accepts only its explicit JSON summary, never raw stderr.
        with tempfile.TemporaryFile() as output:
            try:
                completed = subprocess.run(command, cwd=ROOT, timeout=v['timeout_seconds'],
                                           stdout=output if v['diagnostics'] == 'summary' else subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                result['exit_code'] = completed.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                result.update(exit_code=-1, error_type=type(exc).__name__)
            if v['diagnostics'] == 'summary':
                output.seek(0)
                raw = output.read(65537)
                try:
                    require(len(raw) <= 65536, 'Summary too large')
                    summary = json.loads(raw)['summary']
                    require(text(summary), 'Missing summary')
                    result['summary'] = summary[:v['summary_max_chars']]
                    result['summary_truncated'] = len(summary) > v['summary_max_chars']
                except (ValueError, KeyError, TypeError, UnicodeError):
                    result['diagnostic_error'] = 'Wrapper must emit a bounded JSON object with a summary string'
                    if result['exit_code'] == 0:
                        result['exit_code'] = -1
        results.append(result)
    return results


def task_view(task):
    claim = coord.live_claim(task)
    return {key: task[key] for key in ('id', 'title', 'status', 'criteria', 'dependencies', 'cycle', 'repairs', 'paths')} | {
        'claim': claim, 'built_by': task.get('built_by'), 'reviewed_by': task.get('reviewed_by'),
        'follows': task.get('follows'),
        'data_mode': task['data_mode'], 'analysis': {phase: entry['status'] for phase, entry in task['analysis_state'].items()},
        'pending_question': task.get('data_question'),
        'latest': {kind: task[kind][-1] if task.get(kind) else None for kind in KINDS}}


def context(task, config, role, limit):
    validate_task(task, config)
    definitions = roles.configured(ROOT, config)
    require(role == 'coordinator' or role in definitions, f'Unknown role {role}; configured: {", ".join(definitions)}')
    packet = task_view(task) | {'role': role, 'omitted': [], 'rules': 'AGENTS.md',
                              'validation_stale': bool(task['snapshot']) and task['status'] != 'done' and not fresh(task, config)}
    packet['role_policy'] = roles.policy(definitions[role]) if role != 'coordinator' else None
    if role == 'reviewer' and task['snapshot']:
        packet['review_ref'] = review_ref(task)
    packet['role_instruction_ref'] = role_ref(role) if role != 'coordinator' else 'AGENTS.md'
    enabled = data_enabled(config)
    if enabled:
        packet['analysis_stale'] = analysis_stale(task, config)
        packet['active_analysis'] = task.get('analysis_pending')
        packet['selected_datasets'] = task['datasets']
    candidates = []
    order = ('inspections', 'analyses', 'handoffs', 'checkpoints', 'reviews', 'tests') if role == 'analyzer' else ('handoffs', 'reviews', 'tests', 'inspections', 'analyses', 'checkpoints')
    for kind in order:
        if task.get(kind):
            item = evidence(task, kind)
            if kind == 'checkpoints':
                item = {key: value for key, value in item.items() if key != 'working_snapshot'}
            candidates.append((kind, item))
    if role != 'coordinator':
        candidates.append(('role_instruction', definitions[role]['body']))
    if role == 'analyzer' and config['analysis']['profiling']['enabled'] and config['analysis']['data_access'] == 'approved-summaries' and task.get('profiles'):
        summary = evidence(task, 'profiles')
        if summary['summary']['data_hash'] == inspect_phase(task, config, summary['phase'])['hash']:
            candidates.insert(2, ('profile', summary))
    if enabled:
        candidates.append(('analysis_evidence', task['analysis_state']))
    candidates += [('technology', config['technology']), ('approvals', config['approvals']),
                   ('delegation', config['delegation']), ('validation', config['validation'])]
    if role == 'analyzer':
        candidates.insert(1, ('analysis_policy', {'data_access': config['analysis']['data_access']}))
    if role == 'fetcher':
        candidates.insert(0, ('sources', config['analysis']['sources']))
    if task['checkpoints']:
        checkpoint = evidence(task, 'checkpoints')
        try:
            changed = checkpoint['working_snapshot'] != fingerprint(
                config, task | {'source_files': checkpoint['source_files']})
        except (ValueError, OSError):
            changed = True
        packet['checkpoint_stale'] = (checkpoint['cycle'] != task['cycle']
                                      or checkpoint['status'] != task['status'] or changed)
    for key, value in candidates:
        packet[key] = value
    # Remove entire optional sections, never silently truncate criteria or evidence.
    while len(encoded(packet)) > limit and candidates:
        key, _ = candidates.pop()
        del packet[key]
        packet['omitted'].append(key)
    require(len(encoded(packet)) <= limit, 'Core task exceeds context limit; use show or explicitly increase --max-chars')
    return encoded(packet)


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--agent', help='Agent identity (default: $WORKFLOW_AGENT or local)')
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    add = lambda name: sub.add_parser(name, parents=[common])
    c = add('configure'); c.add_argument('--config', required=True); c.add_argument('--approved', action='store_true')
    c.add_argument('--assumed', help='File listing open items approved as their stated defaults (recorded in the journal)')
    c = add('add'); c.add_argument('--title', required=True); c.add_argument('--criterion', action='append', required=True); c.add_argument('--depends', action='append', default=[])
    c.add_argument('--prompt', help='File with the request'); c.add_argument('--prompt-text', help='The request as text')
    c.add_argument('--follows', help='Task whose review or result this follows up (does not block)')
    c.add_argument('--path', action='append', required=True, help='Glob of files this task may change (repeatable)')
    c.add_argument('--data', choices=['auto', 'expected', 'none'])
    c.add_argument('--data-reason', default='')
    c.add_argument('--dataset', action='append', default=[])
    c.add_argument('--analysis-phase', choices=['pre', 'post'], action='append')
    for name in ('start', 'handoff', 'review', 'test', 'memory', 'done', 'block', 'unblock', 'log', 'checkpoint', 'rebuild', 'show', 'context', 'resume', 'data-check', 'analysis-report', 'data-resolve', 'profile', 'fetch', 'claim', 'release', 'heartbeat', 'scope'):
        c = add(name); c.add_argument('task')
        if name in ('review', 'memory', 'log', 'block', 'checkpoint', 'rebuild', 'analysis-report', 'data-resolve'):
            c.add_argument('--file', required=True)
        if name in ('data-check', 'analysis-report', 'profile'):
            c.add_argument('--phase', choices=['pre', 'post'], required=True)
        if name == 'analysis-report':
            c.add_argument('--verdict', choices=['pass', 'needs-input'], required=True)
            c.add_argument('--question')
        if name == 'data-resolve':
            c.add_argument('--approved', action='store_true')
            c.add_argument('--data', choices=['auto', 'expected', 'none'])
        if name == 'review':
            c.add_argument('--verdict', choices=['pass', 'fail'], required=True)
            c.add_argument('--reviewer', help='Identity of the reviewer (default: the recording agent); an assertion')
        if name == 'handoff':
            c.add_argument('--to', choices=['reviewer', 'tester', 'coordinator'], required=True)
            c.add_argument('--file', help='Handoff fields (templates/handoff.json); default: reuse the previous handoff')
            c.add_argument('--set', action='append', default=[], metavar='FIELD=VALUE',
                           help='Override one field; files/memories take comma-separated paths')
        if name in ('context', 'resume'):
            c.add_argument('--role', default='coordinator')
            c.add_argument('--max-chars', type=int)
        if name in ('claim', 'heartbeat', 'start'):
            c.add_argument('--ttl', type=int, help='Lease minutes (default: coordination.lease_minutes)')
        if name == 'scope':
            c.add_argument('--path', action='append', required=True)
        if name == 'fetch':
            c.add_argument('--source', required=True, help='Name of an approved entry in analysis.sources')
    c = add('decide'); c.add_argument('--file', required=True); c.add_argument('--task'); c.add_argument('--supersedes')
    c = add('journal'); c.add_argument('--task'); c.add_argument('--type', choices=['decision', 'milestone']); c.add_argument('--id'); c.add_argument('--limit', type=int, default=20); c.add_argument('--all', action='store_true'); c.add_argument('--render', action='store_true'); c.add_argument('--full', action='store_true'); c.add_argument('--max-chars', type=int, default=6000)
    add('check')
    add('doctor')
    add('self-test').add_argument('-v', '--verbose', action='store_true')
    c = add('new'); c.add_argument('dest', help='New project directory (must not exist)')
    c.add_argument('--from', dest='source', help='Template checkout to clone (default: this one)')
    add('validate-config').add_argument('--config', required=True)
    c = add('update-config'); c.add_argument('--config', required=True); c.add_argument('--approved', action='store_true'); c.add_argument('--preview-hash')
    add('status').add_argument('--all', action='store_true')
    c = add('recall'); c.add_argument('query'); c.add_argument('--limit', type=int, default=5)
    add('sync-agents')
    add('install-hooks')
    c = add('guard'); c.add_argument('mode', choices=['hook', 'session', 'edit', 'bash', 'commit'])
    c.add_argument('--path'); c.add_argument('--command', dest='shell_command'); c.add_argument('--content-file'); c.add_argument('--role')
    return p


def report_journal_result(result):
    if not result['view_rendered']:
        print(f"Journal record {result['id']} saved; Markdown view failed ({result['view_error']}). "
              'Do not repeat the append. Run workflow.py journal --render to rebuild the view.', file=sys.stderr)
    return result['id']


def milestone(decision, *, rationale, outcome, evidence, task=None):
    return report_journal_result(decision_journal.append(ROOT, kind='milestone', task=task, decision=decision,
                                   rationale=rationale, alternatives=[], outcome=outcome,
                                   status='recorded', evidence=evidence, agent=AGENT))


def config_changes(old, new, path=''):
    if isinstance(old, dict) and isinstance(new, dict):
        result = []
        for key in sorted(set(old) | set(new)):
            result.extend(config_changes(old.get(key), new.get(key), path + '.' + key if path else key))
        return result
    return [] if old == new else [{'path': path, 'before': old, 'after': new}]


def guidance(config):
    """Generated project instructions: shared rules plus enabled add-on rules, never config values."""
    rules = (ROOT / 'templates/agent_rules.md').read_text(encoding='utf-8')
    if data_enabled(config):
        rules += '\n' + (ADDON / 'rules.md').read_text(encoding='utf-8')
    brief = '# Project brief\n\n' + '\n'.join(f'**{key}**: {value}\n' for key, value in config['project'].items())
    return {'AGENTS.md': '# Project agent instructions\n\n' + rules, 'PROJECT.md': brief}


TEMPLATE_README = '# template_llm_mini\n'


def project_readme(config):
    """Starting README for a configured project; the project owns it afterwards."""
    p = config['project']
    checks = '\n'.join(' '.join(command) for command in config['validation']['commands'])
    return '\n'.join([
        f'# {p["name"]}', '', p['purpose'], '',
        f'- **Users:** {p["users"]}', f'- **Scope:** {p["scope"]}', f'- **Out of scope:** {p["exclusions"]}', '',
        '## Acceptance criteria', '', *[f'- {criterion}' for criterion in p['acceptance_criteria']], '',
        '## Working on this project', '',
        'Agents start with AGENTS.md; Claude Code loads its hooks only when the session starts in this',
        'directory. Workflow commands: [docs/workflow.md](docs/workflow.md). Task board: tasks.md.',
        'Decisions: logging/journal.md. Project brief: PROJECT.md.', '',
        '## Checks', '', '```sh', checks, 'python3 scripts/workflow.py doctor', '```', '',
        'Generated once by `workflow.py configure` from the approved brief; edit freely.', ''])


def install_hooks():
    """Point Git at githooks/ so commit checks apply to every agent and human."""
    try:
        inside = subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return 'Git not available; commit hooks not installed.'
    if inside.returncode or inside.stdout.strip() != 'true':
        return 'Not a Git work tree; run `git init` then `workflow.py install-hooks`.'
    for hook in (ROOT / 'githooks').glob('*'):
        hook.chmod(hook.stat().st_mode | 0o111)
    subprocess.run(['git', 'config', 'core.hooksPath', 'githooks'], cwd=ROOT, check=True)
    return 'Git hooks installed (core.hooksPath=githooks).'


def doctor_findings():
    config = read(ROOT / '.workflow/config.json') if (ROOT / '.workflow/config.json').exists() else None
    return doctor.checks(ROOT, config, AGENT_EXPLICIT)


def new_project(args):
    """Clone this (unconfigured) template into a new project directory."""
    source = Path(args.source).expanduser().resolve() if args.source else ROOT
    dest = Path(args.dest).expanduser().resolve()
    require(doctor.git(source, 'rev-parse', '--is-inside-work-tree') == 'true', f'{source} is not a Git work tree')
    require(not (source / '.workflow/config.json').exists(), f'{source} is a configured project, not the template')
    require(not dest.exists(), f'{dest} already exists')
    if doctor.git(source, 'status', '--porcelain'):
        print(f'Warning: uncommitted changes in {source} are not copied; commit them first if needed.', file=sys.stderr)
    subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(source), str(dest)], check=True)
    run = lambda *a: subprocess.run(['git', *a], cwd=dest, check=True, capture_output=True)
    # Keep the template reachable for pulling later fixes, but never as a push target.
    run('remote', 'rename', 'origin', 'template')
    run('config', 'remote.template.pushurl', 'no-push://template-is-read-only')
    for key in ('user.name', 'user.email'):
        value = doctor.git(source, 'config', '--local', '--get', key)
        if value:
            run('config', key, value)
    run('config', 'core.hooksPath', 'githooks')
    print(f'Created {dest} (remote "template" -> {source}, fetch only).\n'
          f'Next: cd {dest}, start your assistant there (Claude Code loads its hooks only from the project root), '
          'and run the configuration interview (CONFIGURE.md). Check setup with workflow.py doctor.')
    return 0


def update_config(args):
    candidate = read(args.config)
    validate_config(candidate)
    current = read(ROOT / '.workflow/config.json')
    state = read(ROOT / '.workflow/state.json')
    require(state.get('schema_version') == VERSION, 'State migration required before updating configuration')
    changes = config_changes(current, candidate)
    previous_guidance = {name: (ROOT / name).read_text(encoding='utf-8') for name in ('AGENTS.md', 'PROJECT.md')}
    new_guidance = guidance(candidate)
    guidance_changes = {name: ''.join(difflib.unified_diff(previous_guidance[name].splitlines(True),
                        new_guidance[name].splitlines(True), fromfile=name, tofile=name))
                        for name in previous_guidance if previous_guidance[name] != new_guidance[name]}
    preview_hash = digest(json.dumps({'current': current, 'state': state, 'candidate': candidate,
                                     'new_guidance': new_guidance, 'previous_guidance': previous_guidance}, sort_keys=True).encode())
    affected = [task['id'] for task in state['tasks'] if task['status'] != 'done'] if changes or guidance_changes else []
    enabled = data_enabled(candidate)
    conflicts = []
    for task in state['tasks']:
        if task['status'] != 'done' and enabled and task['data_mode'] != 'none':
            try:
                da().validate_selection(ROOT, candidate['analysis'], task['datasets'], task['analysis_phases'])
            except ValueError as exc:
                conflicts.append({'task': task['id'], 'issue': str(exc)})
    data_scope_changed = (current.get('analysis', {}).get('datasets') != candidate.get('analysis', {}).get('datasets'))
    removals = prune_targets(candidate)
    preview = {'preview_hash': preview_hash, 'changes': changes, 'affected_tasks': affected,
               'guidance_changes': guidance_changes, 'removals': removals,
               'data_scope_changed': data_scope_changed,
               'conflicts': conflicts, 'action': 'Active evidence will be invalidated; completed history is preserved.'}
    if not args.approved:
        require(args.preview_hash is None, '--preview-hash is only used with --approved')
        print(encoded(preview))
        return 0
    require(args.preview_hash == preview_hash, 'Missing/stale preview hash; preview again and obtain approval')
    require(not conflicts, 'Resolve task selection/phase conflicts before applying this update')
    if not changes and not guidance_changes and not removals:
        print('Configuration unchanged; no writes made.')
        return 0
    decision_journal.load(ROOT)
    previous_state = json.loads(json.dumps(state))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    revision = ROOT / f'.workflow/config-revisions/{stamp}.json'
    dump(revision, {'status': 'prepared', 'preview': preview, 'previous_config': current,
                    'new_config': candidate, 'previous_state': previous_state,
                    'previous_guidance': previous_guidance, 'new_guidance': new_guidance})
    for task in state['tasks']:
        task.setdefault('profiles', [])
        if task['status'] == 'done':
            continue
        planned = task['status'] == 'planned'
        paused = task['status'] in ('analyzing', 'awaiting-data')
        previous_status = task['status']
        previous_pending = task.get('analysis_pending')
        previous_question = task.get('data_question')
        invalidate(task, candidate)
        task['status'] = 'planned' if planned else task['status']
        if not enabled:
            task.update(data_mode='none', data_reason='Data-analysis add-on disabled', datasets=[], analysis_phases=[])
        elif task['data_mode'] != 'none':
            task['datasets'] = da().validate_selection(ROOT, candidate['analysis'], task['datasets'], task['analysis_phases'])
        if data_scope_changed or not enabled:
            task['analysis_state'] = {}
        else:
            # Keep inspected file references so an unrelated config edit cannot
            # silently forget a disappeared dataset. Reports remain in history.
            for entry in task['analysis_state'].values():
                entry['status'] = 'unreviewed'
        task['analysis_pending'] = None
        task['data_question'] = None
        if enabled and not data_scope_changed and paused and previous_pending:
            task['status'] = previous_status
            task['analysis_pending'] = previous_pending | {'resume_status': 'building'}
            task['data_question'] = previous_question
        artifact(task, 'prompts', f'Approved configuration update {revision.relative_to(ROOT)}; reassess data and validation before continuing.\n', 'md')
    dump(ROOT / '.workflow/config.json', candidate)
    sync_gitignore(candidate)
    for name, content in new_guidance.items():
        write(ROOT / name, content)
    prune(removals)
    if claude_enabled(candidate):
        roles.sync(ROOT, roles.configured(ROOT, candidate))
    save(state)
    record = read(revision); record['status'] = 'applied'; dump(revision, record)
    milestone('Configuration update applied', rationale='Coordinator recorded approval of the matching configuration preview.',
              outcome='Active evidence reassessed; completed history preserved.', evidence=[revision.relative_to(ROOT).as_posix()])
    print(f'Configuration applied; revision preserved at {revision.relative_to(ROOT)}.')
    return 0


def take_claim(task, state, config, minutes=None):
    reason = coord.claim_conflicts(state, task, AGENT, config['delegation']['max_concurrency'])
    require(reason is None, reason)
    task['claim'] = coord.new_claim(AGENT, minutes or config['coordination']['lease_minutes'])


def require_access(task, cmd):
    """Another agent's live claim makes a task read-only, except for cross-agent review."""
    claim = coord.live_claim(task)
    require(not claim or claim['agent'] == AGENT or cmd == 'review',
            f'{task["id"]} is claimed by {claim["agent"] if claim else ""} until {claim["expires"] if claim else ""}; '
            'work on your own task or wait for release/expiry')


READ_ONLY = {'status', 'recall', 'show', 'context', 'resume', 'validate-config', 'check', 'doctor', 'new', 'self-test'}
# The harness's own tests; project validation commands cover project tests only.
HARNESS_TESTS = ('scripts/tests', 'addons/data_analysis/tests')


def main(argv=None):
    global AGENT, AGENT_EXPLICIT
    args = parser().parse_args(argv)
    AGENT = coord.current_agent(args.agent)
    AGENT_EXPLICIT = bool(args.agent or os.environ.get('WORKFLOW_AGENT'))
    if args.command == 'guard':
        import guard
        return guard.run(args, AGENT)
    if (not AGENT_EXPLICIT and args.command not in READ_ONLY and args.command != 'journal'
            and (ROOT / '.workflow/config.json').exists()):
        print('Warning: WORKFLOW_AGENT is unset; recording this as agent "local". '
              'Export WORKFLOW_AGENT or pass --agent.', file=sys.stderr)
    if (args.command in READ_ONLY or (args.command == 'journal' and not args.render)
            or (args.command == 'update-config' and not args.approved)):
        return dispatch(args)
    with coord.locked(ROOT):
        return dispatch(args)


def dispatch(args):
    cmd = args.command
    if cmd == 'journal':
        require(args.limit > 0, 'Journal limit must be positive')
        if args.render:
            decision_journal.render(ROOT)
        print(encoded(decision_journal.query(ROOT, task=args.task, kind=args.type, entry_id=args.id,
                                           limit=None if args.all else args.limit, full=args.full, max_chars=args.max_chars)))
        return
    if cmd == 'decide':
        payload = read(args.file)
        require(isinstance(payload, dict) and set(payload) == {'decision', 'rationale', 'alternatives', 'outcome', 'status', 'evidence'},
                'Use templates/decision.json fields')
        if args.task:
            state = read(ROOT / '.workflow/state.json')
            require(state.get('schema_version') == VERSION and any(t['id'] == args.task for t in state['tasks']), 'Unknown task')
        print(report_journal_result(decision_journal.append(ROOT, kind='decision', task=args.task, supersedes=args.supersedes,
                                                            agent=AGENT, **payload)))
        return
    if cmd == 'validate-config':
        validate_config(read(args.config)); print('Configuration valid; no changes made.'); return
    if cmd == 'doctor':
        found = doctor_findings()
        print(doctor.render(found))
        return 2 if any(level == 'warn' for level, _ in found) else 0
    if cmd == 'new':
        return new_project(args)
    if cmd == 'self-test':
        failed = []
        for folder in [f for f in HARNESS_TESTS if (ROOT / f).is_dir()]:
            print(f'== {folder}', flush=True)
            command = [sys.executable, '-m', 'unittest', 'discover', '-s', folder, '-t', folder] + (['-v'] if args.verbose else [])
            if subprocess.run(command, cwd=ROOT).returncode:
                failed.append(folder)
        print('Harness self-test ' + (f'failed: {", ".join(failed)}' if failed else 'passed'))
        return 2 if failed else 0
    if cmd == 'install-hooks':
        if (ROOT / '.workflow/config.json').exists():
            sync_gitignore(read(ROOT / '.workflow/config.json'))
        print(install_hooks()); return
    if cmd == 'update-config':
        return update_config(args)
    if cmd == 'configure':
        require(args.approved, 'Explicit user approval required')
        decision_journal.load(ROOT)
        c = read(args.config); validate_config(c)
        require(not (ROOT / '.workflow/config.json').exists(), 'Already configured; review a migration before reconfiguration')
        assumed = ' '.join(body(args.assumed).split()) if args.assumed else 'none listed'
        for name, content in guidance(c).items():
            write(ROOT / name, content)
        readme = ROOT / 'README.md'
        # Replace only the template's own README; an adopted project's README is kept.
        if not readme.exists() or readme.read_text(encoding='utf-8').startswith(TEMPLATE_README):
            write(readme, project_readme(c))
        dump(ROOT / '.workflow/config.json', c)
        sync_gitignore(c)
        save({'schema_version': VERSION, 'tasks': []})
        removals = prune_targets(c)
        prune(removals)
        generated = roles.sync(ROOT, roles.configured(ROOT, c)) if claude_enabled(c) else []
        milestone('Initial configuration approved', rationale='Coordinator recorded explicit user approval via configure --approved.',
                  outcome='Project brief, instructions, and task state initialized. Pruned: ' + (', '.join(removals) or 'nothing')
                  + '. Assumed defaults: ' + assumed,
                  evidence=['.workflow/config.json', 'PROJECT.md', 'AGENTS.md'])
        print('Configured. Read PROJECT.md and AGENTS.md. Generated: ' + (', '.join(generated) or 'no Claude subagents'))
        print('Pruned: ' + (', '.join(removals) or 'nothing'))
        print(install_hooks())
        print('Setup check (workflow.py doctor):\n' + doctor.render(doctor_findings())); return
    config = read(ROOT / '.workflow/config.json'); validate_config(config)
    state = read(ROOT / '.workflow/state.json')
    require(state.get('schema_version') == VERSION, 'Old state schema; explicit migration required (do not overwrite history)')
    if cmd == 'sync-agents':
        require(claude_enabled(config), 'claude_integration is pruned; nothing to generate')
        print('\n'.join(roles.sync(ROOT, roles.configured(ROOT, config))) or 'No Claude subagents (all roles are codex-only).'); return
    if cmd == 'recall':
        print(encoded(recall(state, args.query, args.limit))); return
    if cmd == 'status':
        print(encoded([task_view(t) for t in state['tasks'] if args.all or t['status'] != 'done'])); return
    if cmd == 'check':
        decision_journal.load(ROOT)
        claimed = [t for t in state['tasks'] if coord.live_claim(t)]
        for index, first in enumerate(claimed):
            for second in claimed[index + 1:]:
                require(first['claim']['agent'] == second['claim']['agent'] or not coord.overlaps(first['paths'], second['paths']),
                        f'{first["id"]} and {second["id"]}: overlapping live claims held by different agents')
        for t in state['tasks']:
            validate_task(t, config, complete=t['status'] == 'done')
            require(not analysis_stale(t, config), f'{t["id"]}: analysis inspection is stale; run data-check')
            if t['snapshot'] and t['status'] in ('reviewing', 'testing'):
                require(fresh(t, config), f'{t["id"]}: validation stale; rebuild required')
        print('Workflow evidence valid; semantic correctness requires review.'); return
    if cmd == 'add':
        decision_journal.load(ROOT)
        require(text(args.title) and strings(args.criterion), 'Title/criteria must not be empty')
        require(set(args.depends) <= {t['id'] for t in state['tasks']}, 'Unknown dependency')
        require(bool(args.prompt) != bool(args.prompt_text), 'Give the request with exactly one of --prompt or --prompt-text')
        require(args.follows is None or any(t['id'] == args.follows for t in state['tasks']), 'Unknown --follows task')
        request = body(args.prompt) if args.prompt else args.prompt_text.strip() + '\n'
        require(text(request), 'Request cannot be empty')
        paths = [coord.normalize_pattern(p) for p in args.path]
        require(all(source_allowed(p) for p in paths), 'Task paths must be source paths, not data/workflow/generated files')
        if data_enabled(config):
            mode = args.data or 'auto'
            require(mode != 'none' or text(args.data_reason), 'Explain why this task does not involve data')
            phases = [phase for phase in ('pre', 'post') if phase in (args.analysis_phase or config['analysis']['phases'])]
            datasets = da().validate_selection(ROOT, config['analysis'], args.dataset, phases)
            require(mode != 'none' or not datasets, 'A no-data task cannot select datasets')
            reason = args.data_reason
        else:
            require(args.data in (None, 'none') and not args.dataset and not args.analysis_phase,
                    'Data options need addons.data_analysis enabled in an approved configuration')
            mode, phases, datasets, reason = 'none', [], [], args.data_reason or 'Data-analysis add-on disabled'
        t = dict(id=f'T{len(state["tasks"]) + 1:03}', title=args.title, criteria=args.criterion,
                 dependencies=args.depends, status='planned', repairs=0, cycle=0, review_pass=False,
                 test_pass=False, snapshot=None, source_files=[], artifacts={}, paths=paths, claim=None,
                 built_by=None, data_mode=mode, data_reason=reason, datasets=datasets,
                 analysis_phases=phases,
                 analysis_state={}, analysis_pending=None, data_question=None, follows=args.follows,
                 **{k: [] for k in KINDS})
        artifact(t, 'prompts', request, 'md'); state['tasks'].append(t)
        save(state)
        milestone('Task created: ' + t['title'], task=t['id'], rationale='A requested feature/task was registered with acceptance criteria.'
                  + (f' Follow-up to {args.follows}.' if args.follows else ''),
                  outcome='Task planned.', evidence=[t['prompts'][0]])
        print(t['id']); return
    matches = [t for t in state['tasks'] if t['id'] == args.task]
    require(matches, 'Unknown task'); t = matches[0]
    if cmd == 'show':
        print(encoded(task_view(t))); return
    if cmd in ('context', 'resume'):
        limit = config['context']['max_chars'] if args.max_chars is None else args.max_chars
        require(2000 <= limit <= 100000, 'Context limit must be 2000..100000 characters')
        print(context(t, config, args.role, limit)); return
    require(t['status'] != 'done' or cmd == 'log', 'Completed tasks are immutable; create a follow-up')
    if cmd in ('data-check', 'analysis-report', 'data-resolve', 'profile', 'fetch'):
        require(data_enabled(config), 'Data commands need addons.data_analysis enabled')
    if cmd != 'claim':
        require_access(t, cmd)
    validate_task(t, config)
    decision_journal.load(ROOT)
    if cmd in ('review', 'test', 'memory', 'done') and t['status'] in ('reviewing', 'testing'):
        for phase in t['analysis_phases']:
            if not analysis_gate(t, config, state, phase):
                return 2
    if cmd in ('review', 'test', 'memory', 'done') or (cmd == 'handoff' and args.to != 'reviewer'):
        if t['snapshot']:
            ensure_fresh(t, config, state)
    if cmd == 'claim':
        take_claim(t, state, config, args.ttl)
        print(f'{t["id"]} claimed by {AGENT} until {t["claim"]["expires"]}')
    elif cmd == 'heartbeat':
        require(t.get('claim') and t['claim']['agent'] == AGENT, f'{AGENT} does not hold the claim on {t["id"]}')
        take_claim(t, state, config, args.ttl)
        print(f'{t["id"]} lease extended until {t["claim"]["expires"]}')
    elif cmd == 'release':
        require(coord.live_claim(t), f'{t["id"]} has no live claim')
        t['claim'] = None
    elif cmd == 'scope':
        paths = [coord.normalize_pattern(p) for p in args.path]
        require(all(source_allowed(p) for p in paths), 'Task paths must be source paths')
        previous = t['paths']; t['paths'] = paths
        if coord.live_claim(t):
            reason = coord.claim_conflicts(state, t, AGENT, config['delegation']['max_concurrency'])
            require(reason is None, reason)
        artifact(t, 'prompts', f'Scope paths changed by {AGENT}: {previous} -> {paths}\n', 'md')
    elif cmd == 'start':
        require(t['status'] == 'planned', {
            'analyzing': f'{t["id"]} is waiting for analysis-report; a pass resumes building automatically',
            'awaiting-data': f'{t["id"]} is waiting for a data answer; use data-resolve',
            'blocked': f'{t["id"]} is blocked; use unblock',
        }.get(t['status'], f'{t["id"]} is already {t["status"]}; use context {t["id"]} --role <role>'))
        require(all(x['status'] == 'done' for x in state['tasks'] if x['id'] in t['dependencies']), 'Dependencies incomplete')
        take_claim(t, state, config, args.ttl)
        t['status'] = 'building'
        if 'pre' in t['analysis_phases'] and not analysis_gate(t, config, state, 'pre'):
            return 2
        assignment(t, 'builder', t['prompts'][0])
    elif cmd == 'handoff':
        h = handoff_payload(t, args); target = args.to
        require((t['status'], target) in (('building', 'reviewer'), ('reviewing', 'tester'), ('testing', 'coordinator')), 'Invalid handoff stage')
        if target == 'reviewer':
            for phase in t['analysis_phases']:
                if not analysis_gate(t, config, state, phase):
                    return 2
            require(all(source_allowed(ref) for ref in h['files']), 'Use source files, not datasets/generated artifacts, in files')
            outside = [ref for ref in h['files'] if not coord.covers(t['paths'], ref)]
            if outside:
                print(f'Warning: handoff files outside {t["id"]} paths (reference-only?): {", ".join(outside)}', file=sys.stderr)
            t['source_files'] = h['files']
            t['snapshot'] = fingerprint(config, t)
            t['built_by'] = AGENT
        else:
            require(t['review_pass'], 'Passing review required')
            require(all(ref in t['snapshot']['files'] for ref in h['files']), 'New source reference needs rebuild and review')
            require(target != 'coordinator' or t['test_pass'], 'Passing tests required')
        ref = artifact(t, 'handoffs', encoded(envelope(t, target=target, acceptance_criteria=t['criteria'],
                       analysis_evidence=t['analysis_state'], **h)) + '\n')
        assignment(t, target, ref)
        t['status'] = {'reviewer': 'reviewing', 'tester': 'testing', 'coordinator': 'testing'}[target]
    elif cmd == 'review':
        require(t['status'] == 'reviewing', 'Reviewer handoff required')
        mode = config['delegation']['review_mode']
        if mode == 'cross-agent':
            require(AGENT != t.get('built_by'), f'cross-agent review: {AGENT} built {t["id"]}; another agent must record the review')
        reviewer = coord.current_agent(args.reviewer) if args.reviewer else AGENT
        if mode in ('independent', 'cross-agent'):
            require(reviewer != t.get('built_by'), f'{mode} review: {reviewer} built {t["id"]}; pass --reviewer NAME '
                    'naming the independent reviewer (recorded as an assertion, like --approved)')
        report = body(args.file)
        token = review_ref(t)
        require(token in report, f'The review report must contain the current review_ref from '
                f'`workflow.py context {t["id"]} --role reviewer` (it changes with each build and cycle)')
        artifact(t, 'reviews', encoded(envelope(t, verdict=args.verdict, reviewer=reviewer, review_ref=token,
                                                report=report)) + '\n')
        t['reviewed_by'] = reviewer
        t['review_pass'] = args.verdict == 'pass'
        if not t['review_pass']:
            invalidate(t, config, repair=True)
    elif cmd == 'test':
        require(t['status'] == 'testing' and t['review_pass'], 'Passing review and tester handoff required')
        results = run_checks(config)
        # A check that edits selected sources invalidates its own evidence.
        unchanged = fresh(t, config)
        artifact(t, 'tests', encoded(envelope(t, results=results, sources_unchanged=unchanged)) + '\n')
        t['test_pass'] = unchanged and all(x['exit_code'] == 0 for x in results)
        if not t['test_pass']:
            invalidate(t, config, repair=True)
        print('Checks passed' if t['test_pass'] else 'Checks failed; inspect test evidence')
    elif cmd == 'memory':
        require(t['status'] == 'testing' and t['test_pass'], 'Passing tests required')
        artifact(t, 'memories', encoded(envelope(t, report=body(args.file))) + '\n')
    elif cmd == 'done':
        require(t['status'] == 'testing', 'Task must be in testing')
        validate_task(t, config, complete=True, live=True)
        t['status'] = 'done'
        t['claim'] = None
    elif cmd == 'profile':
        require(t['status'] in ('building', 'analyzing', 'awaiting-data', 'reviewing', 'testing'), 'Start task before profiling')
        da()
        import profiling
        result = profiling.profile(ROOT, config, t, args.phase)
        require(result['data_hash'] == inspect_phase(t, config, args.phase)['hash'], 'Resolve data inspection before profiling')
        artifact(t, 'profiles', encoded(envelope(t, phase=args.phase, summary=result)) + '\n')
        print('Profile recorded; use analyzer context.')
    elif cmd == 'fetch':
        require(t['status'] in ('building', 'analyzing', 'awaiting-data'), 'Start the task before fetching')
        result = fetching().fetch(ROOT, config, args.source, t['id'])
        artifact(t, 'fetches', encoded(envelope(t, **result)) + '\n')
        print(f'Fetched {len(result["raw"])} raw and {len(result["parsed"])} parsed files from {args.source}. '
              f'Run data-check {t["id"]} --phase pre so the analyzer inspects them.')
    elif cmd == 'data-check':
        require(t['status'] in ('building', 'analyzing', 'awaiting-data', 'reviewing', 'testing'), 'Start the task first')
        if not analysis_gate(t, config, state, args.phase):
            return 2
    elif cmd == 'analysis-report':
        analysis_report(t, config, state, args)
    elif cmd == 'data-resolve':
        require(t['status'] == 'awaiting-data' and args.approved, 'User response and --approved required')
        artifact(t, 'prompts', body(args.file), 'md')
        if args.data:
            t['data_mode'] = args.data
            t['data_reason'] = body(args.file).strip()
        phase = t['analysis_pending']['phase']
        t['analysis_state'][phase]['response_required'] = False
        t['analysis_state'][phase]['status'] = 'recheck'
        if not analysis_gate(t, config, state, phase):
            return 2
    elif cmd == 'checkpoint':
        h = handoff_input(args.file)
        require(all(source_allowed(ref) for ref in h['files']), 'Checkpoint files must be source paths')
        selected = sorted(set(t['source_files'] + h['files']))
        working = fingerprint(config, t | {'source_files': selected})
        artifact(t, 'checkpoints', encoded(envelope(t, status=t['status'], content=h,
                 source_files=selected, working_snapshot=working)) + '\n')
    elif cmd == 'rebuild':
        require(t['status'] in ('reviewing', 'testing'), 'Rebuild applies to reviewed/tested work')
        artifact(t, 'prompts', body(args.file), 'md'); invalidate(t, config)
    elif cmd == 'block':
        require(t['status'] != 'blocked', 'Already blocked')
        t['previous_status'] = t['status']; t['status'] = 'blocked'
        artifact(t, 'prompts', body(args.file), 'md')
    elif cmd == 'unblock':
        require(t['status'] == 'blocked', 'Task is not blocked')
        require(t['repairs'] <= config['handoffs']['max_repairs'], 'Repair allowance exceeded; obtain direction and create a follow-up task')
        t['status'] = t.pop('previous_status', 'building')
    elif cmd == 'log':
        artifact(t, 'prompts', body(args.file), 'md')
    save(state)
    if cmd == 'done':
        milestone('Task completed: ' + t['title'], task=t['id'], rationale='Completion checks accepted current review, tests, memory, and final handoff.',
                  outcome='Task marked done.', evidence=[t['reviews'][-1], t['tests'][-1], t['memories'][-1], t['handoffs'][-1]])
        open_follow_ups = [x['id'] for x in state['tasks'] if x.get('follows') == t['id'] and x['status'] != 'done']
        if open_follow_ups:
            print(f'{t["id"]} done; open follow-ups: {", ".join(open_follow_ups)}')
    if cmd == 'test' and not t['test_pass']:
        return 2
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'Workflow error: {exc}', file=sys.stderr)
        sys.exit(1)
