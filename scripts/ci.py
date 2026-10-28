#!/usr/bin/env python3
"""Dependency-free CI entry points for optional component presence and strict tests."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = 'agent_coordination'
REQUIRED_FILES = ('requirements.txt', 'requirements.lock', 'contracts.py', 'api.py',
                  'service.py', 'bridge.py', 'client.py', 'cli.py', 'mcp_adapter.py',
                  'schemas/operation-v1.schema.json')


class CIError(Exception):
    pass


def component_status(root):
    """Only an explicit workflow prune entry authorizes an absent component."""
    root = Path(root)
    config_path = root / '.workflow/config.json'
    pruned = False
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
            entries = config['prune']
            if (type(config.get('config_version')) is not int or config['config_version'] != 2
                    or not isinstance(entries, list)
                    or not all(isinstance(item, str) for item in entries)):
                raise ValueError('Invalid configuration')
            pruned = COMPONENT in entries
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CIError('Cannot determine approved pruning from workflow configuration') from exc
    folder = root / 'addons' / COMPONENT
    if pruned:
        if folder.exists():
            raise CIError('Component is marked pruned but its directory still exists')
        return {'component': COMPONENT, 'enabled': False, 'reason': 'explicitly_pruned'}
    if not folder.is_dir():
        raise CIError('Coordination component is missing without an approved prune entry')
    missing = [name for name in REQUIRED_FILES if not (folder / name).is_file()]
    if missing:
        raise CIError('Incomplete coordination component: ' + ', '.join(missing))
    if not list((folder / 'tests').glob('test_*.py')):
        raise CIError('Coordination test files are missing')
    return {'component': COMPONENT, 'enabled': True, 'reason': 'present'}


def validate_schema(folder):
    sys.path.insert(0, str(folder))
    contracts = importlib.import_module('contracts')
    expected = contracts.OPERATIONS.json_schema()
    try:
        actual = json.loads((folder / 'schemas/operation-v1.schema.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise CIError('Committed coordination schema is missing or invalid JSON') from exc
    if actual != expected:
        raise CIError('Committed coordination schema differs from the generated operation contract')


def strict_result(result):
    """Require execution, success, and no unexpected unittest skips."""
    if result.testsRun == 0:
        raise CIError('Coordination suite executed zero tests')
    if result.skipped:
        raise CIError('Coordination suite skipped tests; optional CI requires all tests to execute')
    if not result.wasSuccessful():
        raise CIError('Coordination regression tests failed')
    return {'tests_run': result.testsRun, 'skipped': 0}


def run_coordination(root):
    status = component_status(root)
    if not status['enabled']:
        return status
    if sys.version_info[:2] not in ((3, 12), (3, 13)):
        raise CIError('Optional coordination CI requires Python 3.12 or 3.13')
    for dependency in ('fastapi', 'uvicorn', 'pydantic', 'mcp'):
        try:
            importlib.import_module(dependency)
        except ImportError as exc:
            raise CIError('Required coordination dependency cannot be imported: ' + dependency) from exc
    folder = Path(root) / 'addons' / COMPONENT
    validate_schema(folder)
    suite = unittest.defaultTestLoader.discover(str(folder / 'tests'), top_level_dir=str(folder / 'tests'))
    if suite.countTestCases() == 0:
        raise CIError('Coordination test discovery found zero tests')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return dict(status, schema='current', **strict_result(result))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    status = commands.add_parser('coordination-status')
    status.add_argument('--github-output', action='store_true')
    commands.add_parser('coordination-test')
    args = parser.parse_args(argv)
    try:
        if args.command == 'coordination-status':
            result = component_status(args.root)
            if args.github_output:
                output = os.environ.get('GITHUB_OUTPUT')
                if not output:
                    raise CIError('GITHUB_OUTPUT is required with --github-output')
                with open(output, 'a', encoding='utf-8', newline='\n') as handle:
                    handle.write('enabled=' + str(result['enabled']).lower() + '\n')
        else:
            result = run_coordination(args.root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CIError, OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
