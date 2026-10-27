"""Run approved fetch/parse scripts for registered sources into new, versioned data folders.

Scripts write only to the staging directory they are given; nothing reaches data/
unless every step succeeds, and existing originals are never overwritten.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import data_analysis

NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
PYTHON = re.compile(r'python(3(\.\d+)?)?(\.exe)?')
MANIFEST = '.workflow/fetch-manifest.json'
STAGING = '.workflow/fetch-staging'
require = data_analysis.require


def validate_command(root, command, label):
    require(isinstance(command, list) and len(command) >= 2 and all(isinstance(x, str) and x.strip() for x in command),
            f'{label} must be an argument array: ["python3", "path/to/script.py", ...]')
    require(PYTHON.fullmatch(Path(command[0]).name), f'{label} must run a Python script (deterministic work is Python)')
    script = Path(command[1])
    require(not script.is_absolute() and '..' not in script.parts and script.suffix == '.py',
            f'{label} script must be a project-relative .py file')
    if root is not None:
        require((root / script).is_file(), f'{label} script not found: {command[1]}')


def validate_sources(sources, root=None):
    require(isinstance(sources, list), 'analysis.sources must be a list')
    fields = {'name', 'origin', 'locator', 'command', 'target', 'parser', 'parsed_target', 'timeout_seconds'}
    seen = set()
    for spec in sources:
        require(isinstance(spec, dict) and set(spec) == fields, 'Use addons/data_analysis/templates/source.json fields')
        require(isinstance(spec['name'], str) and NAME.fullmatch(spec['name']) and spec['name'] not in seen,
                'Source names must be unique lowercase identifiers')
        seen.add(spec['name'])
        require(all(isinstance(spec[k], str) and spec[k].strip() for k in ('origin', 'locator')),
                'Record source origin/locator, or explicitly state source unavailable')
        validate_command(root, spec['command'], f'Source {spec["name"]} command')
        target = data_analysis.canonical_data_ref(spec['target'])
        require(Path(target).parts[:2] == ('data', 'raw') and len(Path(target).parts) > 2,
                f'Source {spec["name"]} target must be a folder under data/raw/')
        if spec['parser']:
            validate_command(root, spec['parser'], f'Source {spec["name"]} parser')
            parsed = data_analysis.canonical_data_ref(spec['parsed_target'])
            require(Path(parsed).parts[:2] == ('data', 'processed') and len(Path(parsed).parts) > 2,
                    f'Source {spec["name"]} parsed_target must be a folder under data/processed/')
        else:
            require(spec['parsed_target'] == '', f'Source {spec["name"]}: parsed_target must be "" without a parser')
        require(type(spec['timeout_seconds']) is int and 1 <= spec['timeout_seconds'] <= 3600,
                'Source timeout_seconds must be 1..3600')


def manifest(root):
    path = root / MANIFEST
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def run(root, command, env, timeout):
    # Output is discarded: fetch/parse logs can contain record values or credentials.
    command = [sys.executable if PYTHON.fullmatch(command[0]) else command[0], *command[1:]]
    try:
        return subprocess.run(command, cwd=root, env=os.environ | env, timeout=timeout,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:
        return 'timeout'
    except OSError as exc:
        return type(exc).__name__


def collect(base):
    files = []
    for directory, dirs, names in os.walk(base, followlinks=False):
        for name in dirs + names:
            require(not (Path(directory) / name).is_symlink(), 'Fetched output must not contain symlinks')
        files += [Path(directory) / name for name in names]
    return sorted(files)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(root, config, name, task_id):
    sources = {spec['name']: spec for spec in config['analysis']['sources']}
    require(name in sources, f'Unknown source {name}; register it in analysis.sources through update-config')
    spec = sources[name]
    when = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    staging = root / STAGING / f'{name}-{when}'
    raw_stage, parsed_stage = staging / 'raw', staging / 'parsed'
    raw_dest = data_analysis.data_path(root, f'{spec["target"]}/{when}')
    parsed_dest = data_analysis.data_path(root, f'{spec["parsed_target"]}/{when}') if spec['parser'] else None
    require(not raw_dest.exists() and not (parsed_dest and parsed_dest.exists()), 'Fetch destination already exists; retry')
    try:
        raw_stage.mkdir(parents=True)
        code = run(root, spec['command'], {'FETCH_TARGET': str(raw_stage), 'FETCH_SOURCE': name}, spec['timeout_seconds'])
        require(code == 0, f'Fetch command failed ({code}); nothing was written to data/')
        raw_files = collect(raw_stage)
        require(raw_files, 'Fetch command produced no files; nothing was written to data/')
        parsed_files = []
        if spec['parser']:
            parsed_stage.mkdir()
            code = run(root, spec['parser'], {'FETCH_DIR': str(raw_stage), 'PARSE_TARGET': str(parsed_stage),
                                              'FETCH_SOURCE': name}, spec['timeout_seconds'])
            require(code == 0, f'Parser failed ({code}); nothing was written to data/')
            parsed_files = collect(parsed_stage)
            require(parsed_files, 'Parser produced no files; nothing was written to data/')
        # Every step succeeded: publish as new versioned folders, never over originals.
        raw_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(raw_stage), raw_dest)
        if parsed_dest:
            parsed_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(parsed_stage), parsed_dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if staging.parent.is_dir() and not any(staging.parent.iterdir()):
            staging.parent.rmdir()
    fetched_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    ref = lambda base, stage, path: (base / path.relative_to(stage)).relative_to(root).as_posix()
    raw_refs = [ref(raw_dest, raw_stage, p) for p in raw_files]
    parsed_refs = [ref(parsed_dest, parsed_stage, p) for p in parsed_files]
    entries = {path: dict(kind='external', source=spec['origin'], locator=spec['locator'], derived_from=[],
                          fetch_source=name, fetched_at=fetched_at, task=task_id) for path in raw_refs}
    entries.update({path: dict(kind='derived', source='parsed from fetched data', locator='see lineage',
                               derived_from=raw_refs, fetch_source=name, fetched_at=fetched_at, task=task_id)
                    for path in parsed_refs})
    known = manifest(root) | entries
    path = root / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(known, indent=2, sort_keys=True) + '\n')
    return {'source': name, 'origin': spec['origin'], 'locator': spec['locator'], 'fetched_at': fetched_at,
            'raw': [{'path': p, 'sha256': digest(root / p)} for p in raw_refs],
            'parsed': [{'path': p, 'sha256': digest(root / p)} for p in parsed_refs]}
