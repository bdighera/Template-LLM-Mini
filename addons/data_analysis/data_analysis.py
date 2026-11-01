"""Bounded, read-only dataset inspection. Never emits record values or executes inputs."""
import csv
from datetime import date
import math
import re
import hashlib
import io
import json
import os
from pathlib import Path

FORMATS = {'csv', 'json', 'jsonl', 'txt', 'md'}
PHASES = {'pre', 'post'}
UNAVAILABLE = 'source unavailable'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical_data_ref(ref):
    require(isinstance(ref, str) and ref.strip(), 'Dataset path must be text')
    p = Path(ref)
    require(not p.is_absolute() and '..' not in p.parts and p.parts and p.parts[0] == 'data'
            and len(p.parts) > 1, 'Datasets must use paths inside data/')
    return p.as_posix()


def data_path(root, ref):
    p = Path(canonical_data_ref(ref))
    current = root
    for part in p.parts:
        current /= part
        require(not current.is_symlink(), f'Data symlink is not allowed: {ref}')
    return current


def validate_provenance(root, entries):
    require(isinstance(entries, list), 'analysis.provenance must be a list')
    known = {}
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {'path', 'kind', 'source', 'locator', 'derived_from'},
                'Provenance entries need exactly path, kind, source, locator, derived_from '
                '(addons/data_analysis/templates/provenance.json); one entry per exact file path, no globs')
        require(isinstance(entry['path'], str), 'Provenance path must be text')
        data_path(root, entry['path'])
        require(entry['path'] == Path(entry['path']).as_posix(), 'Use canonical data paths without ./ prefixes')
        require(entry['path'] not in known, 'Duplicate provenance path')
        require(entry['kind'] in ('external', 'derived', 'generated', 'unknown'), 'Invalid provenance kind')
        require(all(isinstance(entry[key], str) and entry[key].strip() for key in ('source', 'locator')),
                'Record source/locator, or explicitly state source unavailable')
        require(isinstance(entry['derived_from'], list), 'derived_from must be a list')
        for parent in entry['derived_from']:
            require(isinstance(parent, str), 'Lineage paths must be text')
            data_path(root, parent)
            require(parent == Path(parent).as_posix(), 'Use canonical lineage paths')
        require(entry['kind'] != 'generated' or not entry['derived_from'],
                'Externally derived material must retain attribution; use kind derived')
        known[entry['path']] = entry

    pending = set(known)
    while pending:
        ready = {ref for ref in pending if not (set(known[ref]['derived_from']) & pending)}
        require(ready, 'Provenance lineage must not contain cycles')
        pending -= ready


def fetched(root):
    """Provenance recorded by `workflow.py fetch` (see fetching.py); declared entries take precedence."""
    path = root / '.workflow/fetch-manifest.json' if root is not None else None
    if not path or not path.exists():
        return {}
    keys = ('kind', 'source', 'locator', 'derived_from')
    return {ref: dict({key: entry[key] for key in keys}, path=ref, fetched_at=entry['fetched_at'])
            for ref, entry in json.loads(path.read_text(encoding='utf-8')).items()}


def provenance(config, ref, root=None):
    known = fetched(root) | {item['path']: item for item in config['analysis']['provenance']}

    def describe(path):
        entry = dict(known.get(path, dict(path=path, kind='unknown', source=UNAVAILABLE,
                                        locator=UNAVAILABLE, derived_from=[])))
        if entry['kind'] == 'generated':
            entry.update(source='not required (original generated content)', locator='not applicable', attribution='exempt')
        else:
            entry['attribution'] = 'source unavailable' if entry['source'].strip().lower() == UNAVAILABLE else 'provided; not independently verified'
        return entry

    result = describe(ref)
    pending, seen, lineage = list(result['derived_from']), {ref}, []
    while pending:
        parent = pending.pop(0)
        if parent not in seen:
            seen.add(parent)
            entry = describe(parent)
            lineage.append(entry)
            pending.extend(entry['derived_from'])
    result['lineage'] = lineage
    return result


def validate_settings(settings, root=None):
    require(settings['mode'] == 'auto', 'Analysis mode must be auto; task relevance controls activation')
    require(settings['phases'] in (['pre'], ['post'], ['pre', 'post']), 'Analysis phases must be pre, post, or pre then post')
    require(settings['data_access'] in ('metadata-only', 'approved-summaries'), 'Unsupported analyzer access policy')
    require(type(settings['max_file_bytes']) is int and 1 <= settings['max_file_bytes'] <= 100000000,
            'Analysis max_file_bytes must be 1..100000000')
    require(type(settings['max_files']) is int and 1 <= settings['max_files'] <= 1000,
            'Analysis max_files must be 1..1000')
    require(isinstance(settings['datasets'], list), 'Datasets must be a list')
    seen = set()
    for spec in settings['datasets']:
        fields = {'path', 'phase', 'format', 'required_columns', 'min_records', 'purpose', 'required'}
        require(isinstance(spec, dict) and fields <= set(spec) <= fields | {'column_rules'},
                'Use addons/data_analysis/templates/dataset.json fields')
        require(isinstance(spec['path'], str), 'Dataset path must be text')
        spec['path'] = canonical_data_ref(spec['path'])
        if root is not None:
            data_path(root, spec['path'])
        require(spec['path'] not in seen, 'Duplicate dataset path; use phase both')
        seen.add(spec['path'])
        require(spec['phase'] in PHASES | {'both'},
                f"{spec['path']}: unsupported phase {spec['phase']!r}; use one of {', '.join(sorted(PHASES | {'both'}))}")
        require(spec['format'] in FORMATS,
                f"{spec['path']}: unsupported format {spec['format']!r}; use one of {', '.join(sorted(FORMATS))}")
        require(isinstance(spec['purpose'], str) and spec['purpose'].strip(), 'Dataset purpose is required')
        require(type(spec['required']) is bool, 'Dataset required must be boolean')
        require(type(spec['min_records']) is int and spec['min_records'] >= 1, 'min_records must be positive')
        require(isinstance(spec['required_columns'], list) and all(isinstance(c, str) and c.strip()
                for c in spec['required_columns']), 'required_columns must be string names')
        require(not spec['required_columns'] or spec['format'] in {'csv', 'json', 'jsonl'},
                'Required columns apply only to structured formats')
        validate_column_rules(spec.get('column_rules', {}))
        require(not spec.get('column_rules') or spec['format'] in {'csv', 'json', 'jsonl'},
                'Column rules apply only to structured formats')


def validate_selection(root, settings, selectors, phases, synthetic_root=None):
    require(phases and set(phases) <= PHASES, 'Select pre and/or post phases')
    selected = list(dict.fromkeys(canonical_data_ref(ref) for ref in selectors))
    known = {canonical_data_ref(spec['path']): spec for spec in settings['datasets']}
    for ref in selected:
        data_path(root, ref)
        spec = known.get(ref)
        require(not synthetic_root or not ref.startswith(synthetic_root.rstrip('/') + '/') or spec is not None, 'Synthetic datasets require an explicit analysis.datasets spec with a phase')
        inferred = 'post' if len(Path(ref).parts) > 2 and Path(ref).parts[1] in ('processed', 'generated') else 'pre'
        applicable = PHASES if spec and spec['phase'] == 'both' else {spec['phase'] if spec else inferred}
        require(bool(applicable & set(phases)), f'Dataset selection conflicts with requested phases: {ref}')
    return selected


def strict_json(content):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON keys')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('nonfinite JSON number')
    def number(value):
        result = float(value)
        require(math.isfinite(result), 'nonfinite JSON number')
        return result
    return json.loads(content, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def parse_rows(raw, fmt):
    content = raw.decode('utf-8-sig')
    require(content.strip(), 'empty content')
    if fmt == 'csv':
        reader = csv.reader(io.StringIO(content), strict=True)
        header = next(reader)
        require(bool(header) and all(x.strip() for x in header) and len(header) == len(set(header)),
                'CSV headers must be nonempty and unique')
        rows = []
        for row in reader:
            if not row or not any(x.strip() for x in row):
                continue
            require(len(row) == len(header), 'inconsistent CSV row width')
            rows.append(dict(zip(header, row)))
        return rows
    if fmt in ('json', 'jsonl'):
        rows = strict_json(content) if fmt == 'json' else [strict_json(line) for line in content.splitlines() if line.strip()]
        rows = rows if isinstance(rows, list) else [rows]
        return [row for row in rows if row not in (None, '', {}, [])
                and not (isinstance(row, str) and not row.strip())]
    return [line for line in content.splitlines() if line.strip()]


def validate_column_rules(rules):
    require(isinstance(rules, dict), 'column_rules must be an object')
    for column, rule in rules.items():
        require(isinstance(column, str) and column.strip() and isinstance(rule, dict), 'Invalid column rule')
        require(set(rule) <= {'type', 'nullable', 'unique', 'minimum', 'maximum'}, 'Unknown column rule')
        require('type' not in rule or rule['type'] in ('string', 'integer', 'number', 'boolean', 'date'), 'Unsupported column type')
        for key in ('nullable', 'unique'):
            require(key not in rule or type(rule[key]) is bool, 'Column nullable/unique must be boolean')
        for key in ('minimum', 'maximum'):
            require(key not in rule or (type(rule[key]) in (int, float) and math.isfinite(rule[key])), 'Column bounds must be finite numbers')
        require(not ('minimum' in rule and 'maximum' in rule) or rule['minimum'] <= rule['maximum'], 'Column bounds are reversed')


def missing(value):
    return value is None or isinstance(value, str) and not value.strip()


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def check_columns(rows, rules, fmt):
    for column, rule in rules.items():
        seen = set()
        for row in rows:
            require(isinstance(row, dict) and column in row, 'rule column missing')
            value = row[column]
            if missing(value):
                require(rule.get('nullable', True), 'null/empty value violates column rule')
                continue
            kind = rule.get('type')
            if kind == 'string':
                require(isinstance(value, str), 'column type mismatch')
            elif kind == 'integer':
                require(type(value) is int or (fmt == 'csv' and isinstance(value, str)
                        and re.fullmatch(r'[+-]?\d+', value.strip()) is not None), 'column type mismatch')
            elif kind == 'number':
                require(numeric(value) is not None and (fmt == 'csv' or type(value) in (int, float)), 'column type mismatch')
            elif kind == 'boolean':
                require(type(value) is bool or (fmt == 'csv' and isinstance(value, str)
                        and value.strip().lower() in ('true', 'false')), 'column type mismatch')
            elif kind == 'date':
                try:
                    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is not None, 'column type mismatch')
                    date.fromisoformat(value)
                except ValueError:
                    raise ValueError('column date mismatch') from None
            if 'minimum' in rule or 'maximum' in rule:
                number = numeric(value)
                require(number is not None, 'column range requires finite numeric values')
                require(number >= rule.get('minimum', -math.inf) and number <= rule.get('maximum', math.inf), 'column range mismatch')
            if rule.get('unique'):
                comparable = value
                if kind in ('integer', 'number'):
                    comparable = int(value) if kind == 'integer' else numeric(value)
                elif kind == 'boolean' and fmt == 'csv':
                    comparable = value.strip().lower() == 'true'
                key = json.dumps(comparable, sort_keys=True, ensure_ascii=False)
                require(key not in seen, 'duplicate value violates uniqueness rule')
                seen.add(key)


def records(raw, fmt, required, column_rules=None):
    rows = parse_rows(raw, fmt)
    for row in rows:
        if required:
            require(isinstance(row, dict) and set(required) <= set(row), 'required columns missing')
    check_columns(rows, column_rules or {}, fmt)
    return len(rows)


def scan(root, config, task, phase):
    settings = config['analysis']
    result = {'phase': phase, 'status': 'skipped', 'files': [], 'issues': [], 'reason': '', 'question': None}
    result['contract'] = hashlib.sha256(json.dumps({'settings': settings, 'criteria': task['criteria'],
        'selection': task['datasets']}, sort_keys=True).encode()).hexdigest()
    if task['data_mode'] == 'none' or phase not in task['analysis_phases']:
        result['reason'] = task['data_reason'] or 'Phase not requested'
        return seal(result)
    specs = [s for s in settings['datasets'] if s['phase'] in (phase, 'both')]
    selectors = validate_selection(root, settings, task['datasets'], task['analysis_phases'], synthetic_root=config.get('synthetic', {}).get('root'))
    if selectors:
        specs = [s for s in specs if s['path'] in selectors]
        known = {s['path'] for s in settings['datasets']}
        for ref in selectors:
            if ref not in known:
                kind = Path(ref).parts[1] if len(Path(ref).parts) > 2 else 'other'
                if (phase == 'post') == (kind in ('processed', 'generated')):
                    specs.append(default_spec(ref, phase))
    elif not settings['datasets']:
        base = root / 'data'
        if base.is_symlink():
            result['issues'].append('data/ must not be a symlink')
        elif base.exists():
            for directory, dirs, names in os.walk(base, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
                synthetic_root = config.get('synthetic', {}).get('root')
                dirs[:] = [d for d in dirs if (Path(directory) / d).relative_to(root).as_posix() != synthetic_root]
                if Path(directory) == base:
                    dirs[:] = [d for d in dirs if (phase == 'post') == (d in ('processed', 'generated'))]
                for name in list(dirs):
                    path = Path(directory) / name
                    if path.is_symlink():
                        result['issues'].append(f'Data symlink is not allowed: {path.relative_to(root).as_posix()}')
                        dirs.remove(name)
                for name in sorted(names):
                    if name.startswith('.'):
                        continue
                    ref = (Path(directory) / name).relative_to(root).as_posix()
                    kind = Path(ref).parts[1] if len(Path(ref).parts) > 2 else 'other'
                    if (phase == 'post') == (kind in ('processed', 'generated')):
                        specs.append(default_spec(ref, phase))
                    if len(specs) > settings['max_files']:
                        break
                if len(specs) > settings['max_files']:
                    break
    if len(specs) > settings['max_files']:
        result['issues'].append('Too many selected data files; narrow dataset selection or revise limits')
        specs = specs[:settings['max_files']]
    for spec in specs:
        ref = spec['path']
        item = {'path': ref, 'kind': Path(ref).parts[1] if len(Path(ref).parts) > 2 else 'other',
                'format': spec['format'], 'purpose': spec['purpose'], 'provenance': provenance(config, ref, root)}
        for parent in item['provenance']['lineage']:
            parent['version'] = parent_version(root, parent['path'], settings['max_file_bytes'])
        try:
            path = data_path(root, ref)
            if not path.exists() and not spec['required']:
                continue
            require(path.is_file(), 'expected file missing')
            require(path.stat().st_size <= settings['max_file_bytes'], 'file exceeds configured size limit')
            with path.open('rb') as handle:
                raw = handle.read(settings['max_file_bytes'] + 1)
            require(len(raw) <= settings['max_file_bytes'], 'file exceeds configured size limit')
            item.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            require(spec['format'] in FORMATS, 'unsupported format; approve a Python extractor')
            item['records'] = records(raw, spec['format'], spec['required_columns'], spec.get('column_rules', {}))
            require(item['records'] >= spec['min_records'], 'insufficient usable records')
        except (ValueError, OSError, UnicodeError, csv.Error, StopIteration, RecursionError) as exc:
            # Never persist exception text from parsers: it can contain data values.
            reason = str(exc) if type(exc) is ValueError else type(exc).__name__
            result['issues'].append(f'{ref}: {reason}')
        result['files'].append(item)
    if not result['files'] and not result['issues']:
        # Configured post-only datasets do not imply missing pre-inputs (and vice versa).
        expected = task['data_mode'] == 'expected' and (
            (not settings['datasets'] and phase == task['analysis_phases'][0]) or bool(specs))
        if expected:
            result['issues'].append('Request expects data but no usable selected data was found')
        else:
            result['reason'] = 'No relevant data for this phase'
    if result['issues']:
        result['status'] = 'needs-input'
        result['question'] = ('Data does not meet this task\'s expectations: ' + '; '.join(result['issues'])
                              + '. Please provide the correct data or clarify the approved expectations. '
                              'Should dependent work wait, or should the task scope be revised?')
    elif result['files']:
        result['status'] = 'ready'
    return seal(result)


def default_spec(ref, phase):
    return dict(path=ref, phase=phase, format=Path(ref).suffix.lower().lstrip('.'),
                required_columns=[], column_rules={}, min_records=1, purpose='Task-relevant data; analyzer must assess semantic fit', required=True)


def seal(result):
    result['hash'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def parent_version(root, ref, limit):
    try:
        path = data_path(root, ref)
        if not path.is_file():
            return {'status': 'source unavailable'}
        if path.stat().st_size > limit:
            return {'status': 'not fingerprinted: size limit'}
        with path.open('rb') as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return {'status': 'not fingerprinted: size limit'}
        return {'status': 'observed current parent', 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    except (OSError, ValueError):
        return {'status': 'source unavailable'}
