"""Append-only decision/milestone records and a generated Markdown view; callers hold the workflow lock."""
from datetime import datetime, timezone
import json
import html
from pathlib import Path
import re
from urllib.parse import quote

import coordination

FIELDS = {'id', 'timestamp', 'type', 'task', 'agent', 'decision', 'rationale', 'alternatives',
          'outcome', 'status', 'evidence', 'supersedes'}
LEGACY_FIELDS = FIELDS - {'agent'}  # Records written before agent identity existed.


def require(ok, message):
    if not ok:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def local(root, ref):
    require(text(ref), 'Journal evidence paths must be text')
    path = Path(ref)
    require(not path.is_absolute() and '..' not in path.parts, 'Journal evidence must stay inside the project')
    current = root
    for part in path.parts:
        current /= part
        require(not current.is_symlink(), 'Journal paths must not use symlinks')
    return current


def paths(root):
    return local(root, 'logging/journal.jsonl'), local(root, 'logging/journal.md')


def load(root):
    canonical, _ = paths(root)
    if not canonical.exists():
        return []
    content = canonical.read_text(encoding='utf-8')
    require(not content or content.endswith('\n'), 'Journal has an incomplete final line; restore it before appending')
    entries = []
    superseded = set()
    for number, line in enumerate(content.splitlines(), 1):
        try:
            item = json.loads(line)
        except ValueError:
            raise ValueError(f'Invalid journal JSON at line {number}') from None
        require(isinstance(item, dict) and set(item) in (FIELDS, LEGACY_FIELDS), f'Invalid journal fields at line {number}')
        item.setdefault('agent', None)
        require(item['agent'] is None or text(item['agent']), f'Invalid journal agent at line {number}')
        require(item['id'] == f'J{number:06}', f'Invalid journal sequence at line {number}')
        require(item['type'] in ('decision', 'milestone'), 'Invalid journal record type')
        require(item['task'] is None or isinstance(item['task'], str) and re.fullmatch(r'T\d+', item['task']), 'Invalid journal task')
        require(all(text(item[key]) for key in ('timestamp', 'decision', 'rationale', 'outcome')), 'Journal text must not be empty')
        try:
            stamp = datetime.fromisoformat(item['timestamp'])
            require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0, 'Journal timestamps must be UTC')
        except ValueError:
            raise ValueError('Invalid journal timestamp') from None
        require(item['status'] in (('proposed', 'accepted', 'rejected') if item['type'] == 'decision' else ('recorded',)), 'Invalid journal status')
        for key in ('alternatives', 'evidence'):
            require(isinstance(item[key], list) and all(text(x) for x in item[key]), f'Invalid journal {key}')
        for ref in item['evidence']:
            local(root, ref)  # Historical references may legitimately be archived later.
        target = item['supersedes']
        if target is not None:
            require(item['type'] == 'decision' and item['status'] == 'accepted', 'Only accepted decisions can supersede')
            require(isinstance(target, str) and any(x['id'] == target and x['type'] == 'decision' for x in entries), 'Superseded decision does not exist')
            require(target not in superseded, 'Decision already superseded; target the latest decision instead')
            superseded.add(target)
        entries.append(item)
    return entries



def markdown_text(value):
    # Keep every original byte in JSONL; flatten only the display of multiline text.
    single_line = ' '.join(value.replace('\r\n', '\n').replace('\r', '\n').splitlines())
    escaped = html.escape(single_line, quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+.!|~>\-])', r'\\\1', escaped)


def render(root, entries=None):
    entries = load(root) if entries is None else entries
    _, view = paths(root)
    superseded = {entry['supersedes']: entry['id'] for entry in entries if entry['supersedes']}
    lines = ['# Decision and milestone journal', '',
             'Generated from journal.jsonl; append corrections instead of editing history.', '']
    for original in entries:
        entry = {key: markdown_text(value) if isinstance(value, str) else value for key, value in original.items()}
        status = f'superseded by {superseded[entry["id"]]}' if entry['id'] in superseded else entry['status']
        lines += [f'## {entry["id"]}: {entry["decision"]}', '',
                  f'{entry["timestamp"]} | {entry["type"]} | {entry["task"] or "project"} | {entry["agent"] or "-"} | {status}', '',
                  f'Rationale: {entry["rationale"]}', '', f'Outcome: {entry["outcome"]}', '']
        if entry['alternatives']:
            lines += ['Alternatives: ' + '; '.join(markdown_text(value) for value in entry['alternatives']), '']
        if entry['supersedes']:
            lines += [f'Supersedes: {entry["supersedes"]}', '']
        if entry['evidence']:
            lines += ['Evidence: ' + ', '.join(f'[{markdown_text(ref)}](<../{quote(ref, safe="/")}>)' for ref in entry['evidence']), '']
    coordination.atomic_write(view, '\n'.join(lines) + '\n')


def append(root, *, kind, task, decision, rationale, alternatives, outcome, status, evidence, supersedes=None, agent=None):
    entries = load(root)
    require(kind in ('decision', 'milestone'), 'Invalid journal kind')
    require(task is None or isinstance(task, str) and re.fullmatch(r'T\d+', task), 'Invalid task reference')
    require(all(text(value) for value in (decision, rationale, outcome)), 'Decision, rationale, and outcome are required')
    require(isinstance(alternatives, list) and all(text(x) for x in alternatives), 'Alternatives must be strings')
    require(isinstance(evidence, list) and all(text(x) for x in evidence), 'Evidence must be file references')
    require(status in (('proposed', 'accepted', 'rejected') if kind == 'decision' else ('recorded',)), 'Invalid decision status')
    require(agent is None or text(agent), 'Agent must be text')
    if supersedes is not None:
        require(kind == 'decision' and status == 'accepted', 'Only accepted decisions can supersede')
        require(any(item['id'] == supersedes and item['type'] == 'decision' for item in entries), 'Unknown superseded decision')
        require(not any(item['supersedes'] == supersedes for item in entries), 'Decision already superseded')
    for ref in evidence:
        require(local(root, ref).is_file(), f'Journal evidence missing: {ref}')
    item = dict(id=f'J{len(entries) + 1:06}', timestamp=datetime.now(timezone.utc).isoformat(),
                type=kind, task=task, agent=agent, decision=decision, rationale=rationale, alternatives=alternatives,
                outcome=outcome, status=status, evidence=evidence, supersedes=supersedes)
    canonical, _ = paths(root)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    with canonical.open('a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + '\n')
    # The canonical append has committed. A view error must not look like an
    # unsuccessful append, including unexpected exceptions from a future renderer.
    result = {'id': item['id'], 'record_saved': True, 'view_rendered': True}
    try:
        render(root, entries + [item])
    except Exception as exc:
        result.update(view_rendered=False, view_error=type(exc).__name__,
                      recovery='Record saved; do not repeat the append. Run workflow.py journal --render to rebuild the view.')
    return result


def query(root, *, task=None, kind=None, entry_id=None, limit=20, full=False, max_chars=6000):
    require(limit is None or type(limit) is int and limit > 0, 'Journal limit must be positive')
    require(type(max_chars) is int and 1000 <= max_chars <= 1000000, 'Journal character limit must be 1000..1000000')
    require(type(full) is bool, 'full must be boolean')
    entries = load(root)
    superseded = {entry['supersedes']: entry['id'] for entry in entries if entry['supersedes']}
    selected = [entry | {'superseded_by': superseded.get(entry['id'])} for entry in entries
                if (task is None or (entry['task'] or 'project') == task)
                and (kind is None or entry['type'] == kind) and (entry_id is None or entry['id'] == entry_id)]
    count = len(selected)
    selected = selected if limit is None else selected[-limit:]
    if not full:
        compact = []
        fields = ('id', 'timestamp', 'type', 'task', 'agent', 'decision', 'rationale', 'outcome', 'status', 'supersedes', 'superseded_by')
        for entry in selected:
            result = {key: entry[key] for key in fields}
            clipped = []
            for key in ('decision', 'rationale', 'outcome'):
                if len(result[key]) > 160:
                    result[key] = result[key][:159] + '…'
                    clipped.append(key)
            if clipped:
                result['truncated_fields'] = clipped
            compact.append(result)
        selected = compact
    result = {'entries': selected, 'matching': count, 'omitted': count - len(selected),
              'mode': 'full' if full else 'compact',
              'retrieval_hint': 'Use --id Jxxxxxx --full for original fields; increase --max-chars if omitted.'}
    if not full:
        result.update(excluded_fields=['alternatives', 'evidence'], field_max_chars=160)
    # Match CLI JSON formatting and account for its final newline. Remove whole
    # oldest records, not pieces of original records in full mode.
    while len(json.dumps(result, ensure_ascii=False, indent=2)) + 1 > max_chars and selected:
        selected.pop(0)
        result['omitted'] += 1
    require(len(json.dumps(result, ensure_ascii=False, indent=2)) + 1 <= max_chars,
            'Journal response metadata exceeds character limit')
    return result
