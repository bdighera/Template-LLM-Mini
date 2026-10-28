"""CI boundary regressions: pruning, incomplete components and false-green suites."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness import HarnessTestCase, SOURCE
sys.path.insert(0, str(SOURCE / 'scripts'))
import ci


class CIBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'addons/agent_coordination'

    def complete_fixture(self):
        for name in ci.REQUIRED_FILES + ('tests/test_example.py',):
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Synthetic presence fixture\n', encoding='utf-8')

    def config(self, value):
        path = self.root / '.workflow/config.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_absence_needs_explicit_pruning_and_partial_removal_fails(self):
        with self.assertRaisesRegex(ci.CIError, 'without an approved prune'):
            ci.component_status(self.root)
        self.config({'config_version': 2, 'prune': ['agent_coordination']})
        self.assertFalse(ci.run_coordination(self.root)['enabled'])
        self.folder.mkdir(parents=True)
        with self.assertRaisesRegex(ci.CIError, 'still exists'):
            ci.component_status(self.root)

    def test_present_component_requires_manifest_and_tests(self):
        self.complete_fixture()
        self.assertTrue(ci.component_status(self.root)['enabled'])
        manifest = self.folder / 'requirements.lock'
        manifest.unlink()
        with self.assertRaisesRegex(ci.CIError, 'requirements.lock'):
            ci.component_status(self.root)
        manifest.write_text('# Synthetic lock\n', encoding='utf-8')
        (self.folder / 'tests/test_example.py').unlink()
        with self.assertRaisesRegex(ci.CIError, 'test files are missing'):
            ci.component_status(self.root)

    def test_invalid_prune_metadata_cannot_hide_missing_component(self):
        for value in ({'config_version': 2, 'prune': 'agent_coordination'}, {},
                      {'config_version': 99, 'prune': ['agent_coordination']}):
            self.config(value)
            with self.assertRaises(ci.CIError):
                ci.component_status(self.root)
        (self.root / '.workflow/config.json').write_text('{broken', encoding='utf-8')
        with self.assertRaises(ci.CIError):
            ci.component_status(self.root)

    def test_zero_skipped_and_failed_suites_are_rejected(self):
        class Pass(unittest.TestCase):
            def runTest(self):
                pass
        class Skip(unittest.TestCase):
            def runTest(self):
                self.skipTest('Synthetic missing capability')
        class Fail(unittest.TestCase):
            def runTest(self):
                self.fail('Synthetic regression')
        for cases in ([], [Skip()], [Fail()]):
            result = unittest.TestResult()
            unittest.TestSuite(cases).run(result)
            with self.assertRaises(ci.CIError):
                ci.strict_result(result)
        result = unittest.TestResult()
        unittest.TestSuite([Pass()]).run(result)
        self.assertEqual(ci.strict_result(result), {'tests_run': 1, 'skipped': 0})

    def test_committed_schema_must_match_generator(self):
        self.complete_fixture()
        expected = {'type': 'object', 'properties': {'version': {'const': '1'}}}
        path = self.folder / 'schemas/operation-v1.schema.json'
        contract = SimpleNamespace(OPERATIONS=SimpleNamespace(json_schema=lambda: expected))
        with patch.dict(sys.modules, contracts=contract), patch.object(sys, 'path', list(sys.path)):
            path.write_text(json.dumps(expected), encoding='utf-8')
            ci.validate_schema(self.folder)
            path.write_text(json.dumps({'type': 'string'}), encoding='utf-8')
            with self.assertRaisesRegex(ci.CIError, 'differs'):
                ci.validate_schema(self.folder)
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaisesRegex(ci.CIError, 'invalid JSON'):
                ci.validate_schema(self.folder)

    def test_github_output_is_explicit_and_failures_are_nonzero(self):
        self.complete_fixture()
        output = self.root / 'github-output'
        args = ['--root', str(self.root), 'coordination-status', '--github-output']
        with patch.dict(os.environ, GITHUB_OUTPUT=str(output)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ci.main(args), 0)
        self.assertEqual(output.read_text(encoding='utf-8'), 'enabled=true\n')
        with patch.dict(os.environ, GITHUB_OUTPUT=''), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ci.main(args), 1)
        (self.folder / 'requirements.lock').unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ci.main(['--root', str(self.root), 'coordination-status']), 1)

    def test_missing_dependency_fails_before_suite_can_skip(self):
        self.complete_fixture()
        with patch.object(sys, 'version_info', (3, 12)), patch.object(ci.importlib, 'import_module', side_effect=ImportError('synthetic')):
            with self.assertRaisesRegex(ci.CIError, 'dependency cannot be imported'):
                ci.run_coordination(self.root)


class ConfiguredPruningTests(HarnessTestCase):
    def test_actual_configuration_pruning_is_recognized(self):
        if not (self.root / 'addons/agent_coordination').is_dir():
            self.skipTest('Coordination component already pruned from this checkout')
        self.config['prune'] = ['agent_coordination']
        self.put('config.json', self.config)
        self.configure()
        self.assertFalse((self.root / 'addons/agent_coordination').exists())
        self.assertEqual(ci.component_status(self.root)['reason'], 'explicitly_pruned')
