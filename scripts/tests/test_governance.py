"""Authorization, actual-change evidence, and proportionate completion integration."""
import json
from pathlib import Path
from harness import HarnessTestCase


class GovernanceTests(HarnessTestCase):
    def setup_bounded(self, maintenance='none'):
        self.config['governance'] = dict(mode='bounded', maintenance_approval=maintenance)
        self.put('config.json', self.config)
        self.configure()
        self.git_init()
        self.git('add', '.')
        self.git('config', 'core.hooksPath', 'githooks'); self.git('commit', '-qm', 'Baseline')

    def put(self, name, data):
        if name in ('authorization.json', 'maintenance.json', 'review.md'):
            name = str(Path(self.temp.name) / name)
        super().put(name, data)

    def run_cli(self, *args, **kwargs):
        args = tuple(str(Path(self.temp.name) / a) if a in ('authorization.json', 'maintenance.json', 'review.md') else a for a in args)
        return super().run_cli(*args, **kwargs)

    def authorize(self, paths=None, outputs=None, reference='User approved task plan'):
        self.put('authorization.json', dict(objective='Example', criteria=['Works'], paths=paths or ['example.py'],
            outputs=outputs or [], permissions=dict(data_access=[], dependencies=[], external_activity=[]), approval_reference=reference))
        return self.run_cli('authorize', 'T001', '--file', 'authorization.json', '--approved')

    def test_registration_and_claim_are_not_approval(self):
        self.setup_bounded(); self.add()
        self.run_cli('claim', 'T001')
        self.assertIn('not authorized', self.run_cli('start', 'T001', success=False).stderr)
        self.run_cli('start', 'T001', '--operation-id', 'peer-assignment', success=False)
        self.authorize()
        self.run_cli('authorize', 'T001', '--file', 'authorization.json', success=False)
        self.run_cli('start', 'T001')
        self.assertEqual(self.task()['authorization']['approval_reference'], 'User approved task plan')
        self.assertIn('starting_revision', self.task())

    def test_added_deleted_and_untracked_changes_block_completion(self):
        self.setup_bounded(); self.add(); self.authorize(); self.run_cli('start', 'T001')
        self.put('unrelated.py', 'unrelated = True\n')
        changes = json.loads(self.run_cli('changes', 'T001').stdout)
        self.assertIn('unrelated.py', changes['outside_scope'])
        self.assertIn('outside approved', self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False).stderr)
        (self.root / 'unrelated.py').unlink()
        (self.root / 'README.md').unlink()
        changes = json.loads(self.run_cli('changes', 'T001').stdout)
        self.assertEqual(next(c['status'] for c in changes['changes'] if c['path'] == 'README.md'), 'deleted')

    def test_amendment_and_scope_narrowing_invalidate_evidence(self):
        self.setup_bounded(); self.add(); self.authorize(); self.run_cli('start', 'T001')
        self.handoff('reviewer'); self.review()
        self.run_cli('scope', 'T001', '--path', 'scripts/**', success=False)
        self.authorize(['example.py', 'scripts/**'], reference='User approved expansion')
        self.assertFalse(self.task()['review_pass'])
        self.run_cli('scope', 'T001', '--path', 'scripts/**')
        self.run_cli('scope', 'T001', '--path', 'scripts/new.py')
        self.assertEqual(self.task()['paths'], ['scripts/new.py'])

    def test_data_output_hashes_invalidate_review(self):
        self.setup_bounded(); self.add(); self.authorize(outputs=['data/generated/example']); self.run_cli('start', 'T001')
        self.put('data/generated/example/result.json', '{}')
        self.handoff('reviewer'); self.review(); self.handoff('tester')
        self.put('data/generated/example/result.json', '{"changed":true}')
        self.assertIn('passes invalidated', self.run_cli('test', 'T001', success=False).stderr)
        self.assertFalse(self.task()['review_pass'])

    def test_maintenance_standing_authorization_and_reclassification(self):
        self.setup_bounded('User approved narrow maintenance policy')
        self.run_cli('add', '--title', 'Correction', '--criterion', 'Works', '--prompt-text', 'Correct typo', '--path', 'example.py', '--class', 'maintenance')
        self.run_cli('start', 'T001')
        self.put('maintenance.json', dict(changes='Corrected typo', verification=[], verification_reason='Read the corrected text and checked the diff', handoff='Correction complete'))
        self.run_cli('maintenance-finish', 'T001', '--file', 'maintenance.json')
        self.assertEqual(self.task()['status'], 'done')
        self.run_cli('check')
        self.assertFalse(self.task()['reviews'])

    def test_maintenance_without_standing_policy_and_standard_promotion(self):
        self.setup_bounded()
        self.run_cli('add', '--title', 'Correction', '--criterion', 'Works', '--prompt-text', 'Correct typo', '--path', 'example.py', '--class', 'maintenance')
        self.run_cli('start', 'T001', success=False)
        self.run_cli('classify', 'T001', '--class', 'standard')
        self.run_cli('start', 'T001', success=False)
        self.authorize(); self.run_cli('start', 'T001')
        self.run_cli('maintenance-finish', 'T001', '--file', 'templates/maintenance.json', success=False)

    def test_coverage_does_not_infer_edit_hooks(self):
        self.setup_bounded(); self.add(); self.authorize(); self.run_cli('start', 'T001')
        p = json.loads(self.run_cli('context', 'T001', '--role', 'builder').stdout)
        self.assertEqual(p['enforcement']['edit_interception']['status'], 'unknown')
        self.assertEqual(p['enforcement']['completion_scope']['status'], 'automatically-enforced')
        self.assertIn('Enforcement coverage', self.run_cli('doctor').stdout)

    def test_standard_full_completion_and_reviewed_output_changes(self):
        self.setup_bounded(); self.add(); self.authorize(); self.run_cli('start', 'T001')
        self.put('example.py', 'assert 3 + 3 == 6\n')
        self.handoff('reviewer'); self.review(); self.handoff('tester')
        self.finish(); self.run_cli('check')
        self.assertEqual(self.task()['status'], 'done')

    def test_guard_covers_source_outside_fingerprint_roots(self):
        self.setup_bounded(); self.add(); self.run_cli('claim', 'T001')
        self.assertIn('not authorized', self.run_cli('guard', 'edit', '--path', 'example.py', success=False).stdout)
        self.authorize(); self.run_cli('start', 'T001')
        self.run_cli('guard', 'edit', '--path', 'example.py')
        self.run_cli('guard', 'edit', '--path', 'README.md', success=False)

    def test_maintenance_command_failure_and_source_drift_do_not_complete(self):
        self.setup_bounded('User approved maintenance');
        self.run_cli('add', '--title', 'Correction', '--criterion', 'Works', '--prompt-text', 'Correct typo', '--path', 'example.py', '--class', 'maintenance')
        self.run_cli('start', 'T001')
        import sys
        self.put('maintenance.json', dict(changes='Correction', verification=[[sys.executable, '-c', 'raise SystemExit(3)']], verification_reason='Executed check', handoff='Pending'))
        self.run_cli('maintenance-finish', 'T001', '--file', 'maintenance.json', success=False)
        self.assertEqual(self.task()['status'], 'building')

    def test_released_claim_cannot_complete_maintenance(self):
        self.setup_bounded('Approved maintenance')
        self.run_cli('add', '--title', 'Correction', '--criterion', 'Works', '--prompt-text', 'Correct typo', '--path', 'example.py', '--class', 'maintenance')
        self.run_cli('start', 'T001'); self.run_cli('release', 'T001')
        self.put('maintenance.json', dict(changes='Correction', verification=[], verification_reason='Manual diff check', handoff='Complete'))
        self.assertIn('live claim', self.run_cli('maintenance-finish', 'T001', '--file', 'maintenance.json', success=False).stderr)
        self.assertEqual(self.task()['status'], 'building')

    def test_legacy_snapshot_shape_and_hash_are_unchanged(self):
        import hashlib
        self.configure(); self.add(); self.run_cli('start', 'T001'); self.handoff('reviewer')
        task = self.task(); snapshot = task['snapshot']
        payload = dict(files=snapshot['files'], config=self.config, criteria=task['criteria'], data={})
        self.assertEqual(snapshot['hash'], hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
        self.assertNotIn('governance', snapshot)

    def test_adopting_bounded_policy_for_active_task_establishes_baseline(self):
        self.configure(); self.git_init(); self.git('config', 'core.hooksPath', 'githooks')
        self.git('add', '.'); self.git('commit', '-qm', 'Legacy baseline')
        self.add(); self.run_cli('start', 'T001')
        self.config['governance'] = dict(mode='bounded', maintenance_approval='none')
        path = str(Path(self.temp.name) / 'candidate.json'); self.put(path, self.config)
        preview = json.loads(self.run_cli('update-config', '--config', path).stdout)
        self.run_cli('update-config', '--config', path, '--approved', '--preview-hash', preview['preview_hash'])
        # Configuration adoption is a separately reviewed baseline change.
        self.git('add', '.'); self.git('commit', '-qm', 'Adopt bounded governance')
        self.authorize(); self.handoff('reviewer'); self.review()
        self.assertTrue(self.task()['starting_revision'])
        self.assertIn('governance', self.task()['snapshot'])

    def test_mode_only_changes_are_in_actual_scope_evidence(self):
        import os
        if os.name == 'nt':
            self.skipTest('Windows does not expose Git executable-mode changes')
        self.setup_bounded(); self.add(); self.authorize(); self.run_cli('start', 'T001')
        self.git('config', 'core.filemode', 'true')
        path = self.root / 'README.md'; path.chmod(path.stat().st_mode | 0o100)
        manifest = json.loads(self.run_cli('changes', 'T001').stdout)
        self.assertIn('README.md', manifest['outside_scope'])
        change = next(c for c in manifest['changes'] if c['path'] == 'README.md')
        self.assertEqual(change['before'], change['after'])
        self.assertNotEqual(change['before_mode'], change['after_mode'])

    def test_output_and_manifest_evidence_is_retained_in_snapshot(self):
        self.setup_bounded(); self.add(); self.authorize(outputs=['data/generated/example']); self.run_cli('start', 'T001')
        self.put('data/generated/example/results.json', '{}'); self.handoff('reviewer')
        saved = self.task()['snapshot']['governance']
        self.assertEqual(len(saved['outputs']['data/generated/example/results.json']), 64)
        self.assertEqual(saved['changes']['starting_revision'], self.task()['starting_revision'])
        self.assertEqual(saved['authorization']['approval_reference'], 'User approved task plan')

    def test_output_aliases_cannot_bypass_raw_boundary(self):
        self.setup_bounded(); self.add()
        for ref in ('data/RAW', 'data//raw', 'data/./RAW', 'data/generated/*'):
            self.put('authorization.json', dict(objective='Example', criteria=['Works'], paths=['example.py'], outputs=[ref],
                permissions=dict(data_access=[], dependencies=[], external_activity=[]), approval_reference='User approval'))
            self.run_cli('authorize', 'T001', '--file', 'authorization.json', '--approved', success=False)

    def test_first_configured_task_requires_reviewed_setup_baseline(self):
        self.config['governance'] = dict(mode='bounded', maintenance_approval='none')
        self.put('config.json', self.config)
        self.git_init(); self.git('config', 'core.hooksPath', 'githooks')
        self.git('add', '.'); self.git('commit', '-qm', 'Unconfigured template copy')
        result = self.run_cli('configure', '--config', 'config.json', '--approved')
        self.assertIn('review and commit', result.stdout)
        self.add(); self.authorize()
        self.assertIn('outside approved', self.run_cli('start', 'T001', success=False).stderr)
        self.git('add', '.'); self.git('commit', '-qm', 'Reviewed configuration baseline')
        self.run_cli('start', 'T001')
        self.assertEqual(self.task()['status'], 'building')
