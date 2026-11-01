"""Local synthetic test datasets and isolated runs. No generators or downloads execute here."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid

import governance


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value),
            'IDs/versions must use 1..80 letters, digits, underscores or hyphens')
    return value


def validate_settings(wf, settings):
    require(isinstance(settings, dict) and set(settings) == {'root', 'disposable_cleanup_approval'},
            'synthetic: expected root and disposable_cleanup_approval')
    ref = settings['root']; wf.local(ref)
    require(Path(ref).as_posix() == ref and len(Path(ref).parts) >= 2 and Path(ref).parts[0] == 'data'
            and Path(ref).parts[1].casefold() not in ('raw', 'processed', 'generated') and not any(c in ref for c in '*?[]'),
            'synthetic.root must be a separate canonical local folder under data/')
    require(wf.text(settings['disposable_cleanup_approval']), 'Provide cleanup approval reference or none')


def settings(wf, config):
    require('synthetic' in config, 'Adopt synthetic settings through approved configuration first')
    validate_settings(wf, config['synthetic'])
    return config['synthetic']


def base(wf, config):
    return wf.local(settings(wf, config)['root'])


def location(wf, config, kind, *ids):
    return wf.local((base(wf, config) / kind / Path(*(identifier(v) for v in ids))).relative_to(wf.ROOT).as_posix())


def files(wf, folder):
    require(folder.is_dir(), 'Synthetic folder is unavailable')
    result = {}
    for item in sorted(folder.rglob('*')):
        item = wf.local(item.relative_to(wf.ROOT).as_posix())
        if item.is_file() and item.name != '.gitkeep':
            result[item.relative_to(folder).as_posix()] = governance.hash_file(item)
    return result


def manifest(wf, folder):
    path = wf.local((folder / 'manifest.json').relative_to(wf.ROOT).as_posix())
    require(path.is_file(), 'Synthetic manifest is unavailable')
    result = wf.read(path)
    require(isinstance(result, dict) and result.get('schema_version') == 1 and result.get('synthetic') is True,
            'Unsupported synthetic manifest')
    return result


def verify(wf, config, dataset, version):
    folder = location(wf, config, 'datasets', dataset, version)
    record = manifest(wf, folder)
    require(record.get('kind') == 'dataset' and record.get('dataset_id') == dataset and record.get('version') == version,
            'Dataset identity mismatch')
    actual = files(wf, folder); actual.pop('manifest.json', None)
    require(actual and actual == record.get('files'), 'Synthetic dataset content/hash mismatch')
    return record


def metadata(wf, path):
    value = wf.read(path)
    require(isinstance(value, dict) and set(value) == {'purpose', 'origin', 'generator_revision', 'parameters', 'seed',
        'reproducibility', 'sensitivity', 'retention', 'lineage'}, 'Use templates/synthetic-metadata.json')
    require(all(wf.text(value[k]) for k in ('purpose', 'origin', 'generator_revision', 'reproducibility', 'sensitivity', 'retention')),
            'Metadata fields must be explicit; use unknown or not applicable where appropriate')
    require(isinstance(value['parameters'], dict) and wf.strings(value['lineage']), 'Parameters must be an object; lineage must be a list')
    require(value['seed'] is None or type(value['seed']) in (str, int), 'Seed must be text, integer, or null')
    return value


def own(wf, config, state, task_id):
    tasks = [t for t in state['tasks'] if t['id'] == task_id]
    require(tasks, 'Unknown task')
    task = tasks[0]
    claim = wf.coord.live_claim(task)
    require(claim and claim['agent'] == wf.AGENT, 'Synthetic mutations require your own live task claim')
    require(task['status'] == 'building', 'Synthetic mutations require a building task; rebuild before changing reviewed outputs')
    require(task.get('task_class', 'standard') == 'standard', 'Synthetic lifecycle changes require a standard task')
    governance.require_authorized(wf, task, config)
    return task


def permitted(wf, config, task, destination):
    if governance.bounded(config):
        name = destination.relative_to(wf.ROOT).as_posix()
        require(any(name == ref or name.startswith(ref.rstrip('/') + '/') for ref in task['authorization']['outputs']),
                'Synthetic destination is outside declared output locations; amend authorization')


def load_run(wf, config, task, run_id, state=None, cleanup=False):
    folder = location(wf, config, 'runs', run_id)
    record = manifest(wf, folder)
    require(record.get('kind') == 'run' and record.get('run_id') == run_id,
            'Run identity mismatch')
    owner = next((t for t in (state or {}).get('tasks', []) if t['id'] == record.get('task')), None)
    require(record.get('task') == task['id'] or (cleanup and owner and owner['status'] in wf.coord.FINISHED),
            'Run belongs to another active task')
    return folder, record


def check_inputs(wf, config, record):
    for selected in record['inputs']:
        current = verify(wf, config, selected['dataset_id'], selected['version'])
        require(wf.digest(json.dumps(current, sort_keys=True).encode()) == selected['manifest_hash'], 'Selected dataset manifest changed')


def dispatch(wf, args, config, state):
    settings(wf, config)
    if args.command in ('synthetic-verify', 'synthetic-resolve'):
        value = verify(wf, config, args.dataset, args.version)
        print(wf.encoded({'dataset_id': value['dataset_id'], 'version': value['version'], 'verified_files': len(value['files']),
                          'path': location(wf, config, 'datasets', args.dataset, args.version).relative_to(wf.ROOT).as_posix()}))
        return
    task = own(wf, config, state, args.task)
    if args.command == 'synthetic-run':
        value = metadata(wf, args.file)
        run_id = identifier(args.run or uuid.uuid4().hex)
        folder = location(wf, config, 'runs', run_id); permitted(wf, config, task, folder)
        inputs = []
        for selection in args.dataset:
            require(selection.count('@') == 1, 'Select dataset-id@version explicitly')
            dataset, version = selection.split('@')
            selected = verify(wf, config, dataset, version)
            inputs.append(dict(dataset_id=dataset, version=version, manifest_hash=wf.digest(json.dumps(selected, sort_keys=True).encode())))
        require(not folder.exists(), 'Run already exists; choose a fresh run ID')
        folder.mkdir(parents=True)
        try:
            (folder / 'intermediate').mkdir(); (folder / 'outputs').mkdir()
            wf.dump(folder / 'manifest.json', dict(schema_version=1, kind='run', synthetic=True, run_id=run_id,
                task=task['id'], created_by=wf.AGENT, created_at=datetime.now(timezone.utc).isoformat(),
                disposable=args.disposable, state='open', inputs=inputs, metadata=value, files={}))
        except Exception:
            shutil.rmtree(folder)
            raise
        print(folder.relative_to(wf.ROOT).as_posix())
        return
    folder, record = load_run(wf, config, task, args.run, state=state, cleanup=args.command == 'synthetic-clean')
    if args.command == 'synthetic-seal':
        permitted(wf, config, task, folder)
        require(record['state'] == 'open', 'Run is already sealed')
        check_inputs(wf, config, record)
        hashes = files(wf, folder); hashes.pop('manifest.json', None)
        require(any(ref.startswith('outputs/') for ref in hashes), 'Run has no outputs')
        record.update(state='sealed', files=hashes)
        wf.dump(folder / 'manifest.json', record)
        print('Run sealed; output hashes recorded.')
    elif args.command == 'synthetic-publish':
        require(record['state'] == 'sealed', 'Seal the run before publishing a reusable dataset')
        check_inputs(wf, config, record)
        hashes = files(wf, folder); hashes.pop('manifest.json', None)
        require(hashes == record['files'], 'Sealed run content/hash mismatch')
        target = location(wf, config, 'datasets', args.dataset, args.version); permitted(wf, config, task, target)
        require(not target.exists(), 'Dataset version already exists; publish a new version')
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.publish-', dir=target.parent))
        try:
            output_hashes = {ref[len('outputs/'):]: value for ref, value in record['files'].items() if ref.startswith('outputs/')}
            require(output_hashes, 'Sealed run has no outputs')
            for ref, expected in output_hashes.items():
                require(ref != 'manifest.json', 'Root manifest.json is reserved for dataset metadata')
                src = wf.local((folder / 'outputs' / ref).relative_to(wf.ROOT).as_posix())
                out = staging / ref; out.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, out)
                require(governance.hash_file(out) == expected, 'Output changed during publication')
            content = files(wf, staging)
            wf.dump(staging / 'manifest.json', dict(schema_version=1, kind='dataset', synthetic=True,
                dataset_id=args.dataset, version=args.version, source_run=args.run, metadata=record['metadata'],
                inputs=record['inputs'], files=content))
            require(not target.exists(), 'Dataset version appeared during publication')
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        print(target.relative_to(wf.ROOT).as_posix())
    elif args.command == 'synthetic-clean':
        permitted(wf, config, task, folder)
        standing = config['synthetic']['disposable_cleanup_approval']
        require(args.approved or (record['disposable'] and standing.strip().lower() != 'none'),
                'Deletion requires --approved or an approved disposable-run cleanup policy')
        # Durable receipt preserves lineage before removing explicitly selected run data.
        wf.artifact(task, 'prompts', wf.encoded({'synthetic_cleanup': args.run, 'manifest': record,
            'approval_reference': 'explicit --approved assertion' if args.approved else standing}) + '\n')
        wf.save(state)
        shutil.rmtree(folder)
        print('Run removed; cleanup receipt retained.')
