"""Disposable-copy lifecycle tests: synthetic identity, isolation, integrity and retention."""
import json
from pathlib import Path
from harness import HarnessTestCase


class SyntheticTests(HarnessTestCase):
    def prepare(self, cleanup='none', root='data/synthetic'):
        self.config['governance'] = dict(mode='bounded', maintenance_approval='none')
        self.config['synthetic'] = dict(root=root, disposable_cleanup_approval=cleanup)
        self.put('config.json', self.config); self.configure(); self.git_init()
        self.git('add', '.'); self.git('config', 'core.hooksPath', 'githooks'); self.git('commit', '-qm', 'Baseline')
        self.add()
        self.external('authorization.json', dict(objective='Synthetic integration', criteria=['Works'], paths=['example.py'],
            outputs=[root], permissions=dict(data_access=['Versioned synthetic inputs'], dependencies=[], external_activity=[]),
            approval_reference='User approved synthetic lifecycle test'))
        self.run_cli('authorize', 'T001', '--file', str(self.external_path('authorization.json')), '--approved')
        self.run_cli('start', 'T001')
        self.external('metadata.json', json.loads((self.root / 'templates/synthetic-metadata.json').read_text()))
        self.data_root = root

    def external_path(self, name):
        return Path(self.temp.name) / name

    def external(self, name, value):
        self.external_path(name).write_text(json.dumps(value))

    def create_run(self, name='run1', disposable=False, inputs=()):
        args = ['synthetic-run', 'T001', '--run', name, '--file', str(self.external_path('metadata.json'))]
        if disposable:
            args += ['--disposable']
        for selected in inputs:
            args += ['--dataset', selected]
        return self.run_cli(*args)

    def output(self, run='run1', value='{"synthetic": true}'):
        self.put(f'{self.data_root}/runs/{run}/outputs/records.json', value)

    def publish(self, run='run1', version='v1'):
        self.run_cli('synthetic-seal', 'T001', '--run', run)
        self.run_cli('synthetic-publish', 'T001', '--run', run, '--dataset', 'cohort', '--version', version)

    def test_versioned_input_selection_and_isolated_runs(self):
        self.prepare(); self.create_run(); self.output(); self.publish()
        self.run_cli('synthetic-verify', '--dataset', 'cohort', '--version', 'v1')
        self.create_run('run2', inputs=['cohort@v1']); self.create_run('run3', inputs=['cohort@v1'])
        self.output('run2', 'two'); self.output('run3', 'three')
        self.assertEqual((self.root / self.data_root / 'runs/run2/outputs/records.json').read_text(), 'two')
        self.run_cli('synthetic-publish', 'T001', '--run', 'run1', '--dataset', 'cohort', '--version', 'v1', success=False)
        self.run_cli('synthetic-run', 'T001', '--run', 'run2', '--file', str(self.external_path('metadata.json')), success=False)
        self.assertIn('immutable', self.run_cli('guard', 'edit', '--path', f'{self.data_root}/datasets/cohort/v1/records.json', success=False).stdout)

    def test_hash_mismatch_and_dataset_manifest_changes_rejected(self):
        self.prepare(); self.create_run(); self.output(); self.publish()
        self.create_run('consumer', inputs=['cohort@v1']); self.output('consumer')
        self.put(f'{self.data_root}/datasets/cohort/v1/records.json', 'tampered')
        self.run_cli('synthetic-verify', '--dataset', 'cohort', '--version', 'v1', success=False)
        self.run_cli('synthetic-seal', 'T001', '--run', 'consumer', success=False)

    def test_sealed_run_changes_cannot_be_published(self):
        self.prepare(); self.create_run(); self.output()
        self.run_cli('synthetic-seal', 'T001', '--run', 'run1'); self.output(value='changed')
        self.run_cli('synthetic-publish', 'T001', '--run', 'run1', '--dataset', 'cohort', '--version', 'v1', success=False)

    def test_cleanup_requires_disposable_policy_or_separate_approval(self):
        self.prepare(cleanup='User approved disposable-run cleanup')
        self.create_run(disposable=True); self.output(); self.publish()
        self.run_cli('synthetic-clean', 'T001', '--run', 'run1')
        self.run_cli('synthetic-verify', '--dataset', 'cohort', '--version', 'v1')
        self.create_run('retained')
        self.run_cli('synthetic-clean', 'T001', '--run', 'retained', success=False)
        self.run_cli('synthetic-clean', 'T001', '--run', 'retained', '--approved')
        self.assertTrue(any('synthetic_cleanup' in (self.root / ref).read_text() for ref in self.task()['prompts']))

    def test_no_cleanup_policy_and_no_authorized_output(self):
        self.prepare(); self.create_run(disposable=True)
        self.run_cli('synthetic-clean', 'T001', '--run', 'run1', success=False)
        auth = json.loads(self.external_path('authorization.json').read_text()); auth['outputs'] = []
        self.external('authorization.json', auth)
        self.run_cli('authorize', 'T001', '--file', str(self.external_path('authorization.json')), '--approved')
        self.run_cli('synthetic-run', 'T001', '--run', 'run2', '--file', str(self.external_path('metadata.json')), success=False)

    def test_relocated_root_symlinks_and_unapproved_worker(self):
        self.prepare(root='data/test-material'); self.create_run(); self.output(); self.publish()
        self.run_cli('synthetic-verify', '--dataset', 'cohort', '--version', 'v1')
        self.run_cli('synthetic-clean', 'T001', '--run', 'run1', '--approved', agent='another', success=False)
        link = self.root / self.data_root / 'runs/run1/outputs/link'
        try:
            link.symlink_to(self.external_path('metadata.json'))
        except OSError:
            self.skipTest('Filesystem does not permit symlinks')
        self.run_cli('synthetic-publish', 'T001', '--run', 'run1', '--dataset', 'cohort', '--version', 'v2', success=False)
        self.run_cli('synthetic-verify', '--dataset', '../cohort', '--version', 'v1', success=False)

    def test_reserved_roots_are_case_insensitive(self):
        for root in ('data/raw', 'data/RAW', 'data/Processed', 'data/GENERATED', 'data/*'):
            self.config['synthetic'] = dict(root=root, disposable_cleanup_approval='none')
            self.put('config.json', self.config)
            self.run_cli('validate-config', '--config', 'config.json', success=False)

    def test_lineage_references_remain_compact(self):
        self.prepare(); self.create_run(); self.output(); self.publish()
        self.create_run('second', inputs=['cohort@v1']); self.output('second'); self.publish('second', 'v2')
        second = json.loads((self.root / self.data_root / 'datasets/cohort/v2/manifest.json').read_text())
        self.assertEqual(set(second['inputs'][0]), {'dataset_id', 'version', 'manifest_hash'})
        self.assertEqual(len(second['inputs'][0]['manifest_hash']), 64)
        resolved = json.loads(self.run_cli('synthetic-resolve', '--dataset', 'cohort', '--version', 'v2').stdout)
        self.assertEqual(resolved['path'], f'{self.data_root}/datasets/cohort/v2')

    def test_completed_run_can_be_cleaned_by_a_new_authorized_task(self):
        self.prepare(); self.create_run(disposable=True); self.output(); self.publish()
        self.handoff('reviewer')
        packet = json.loads(self.run_cli('context', 'T001', '--role', 'reviewer').stdout)
        review = self.external_path('review.txt'); review.write_text('review_ref: ' + packet['review_ref'] + '\nReviewed synthetic integration against Works; no findings.\n')
        self.run_cli('review', 'T001', '--verdict', 'pass', '--file', str(review), '--reviewer', 'independent')
        self.handoff('tester'); self.finish()
        self.add()
        self.run_cli('authorize', 'T002', '--file', str(self.external_path('authorization.json')), '--approved')
        self.run_cli('start', 'T002')
        self.run_cli('synthetic-clean', 'T002', '--run', 'run1', '--approved')
        self.run_cli('synthetic-verify', '--dataset', 'cohort', '--version', 'v1')

    def test_run_manifest_guard_allows_nested_output_manifests(self):
        self.prepare(); self.create_run()
        self.run_cli('guard', 'edit', '--path', f'{self.data_root}/runs/run1/manifest.json', success=False)
        self.run_cli('guard', 'edit', '--path', f'{self.data_root}/runs/run1/MANIFEST.JSON', success=False)
        self.run_cli('guard', 'edit', '--path', f'{self.data_root}/runs/run1/outputs/nested/manifest.json')
        self.run_cli('guard', 'edit', '--path', f'{self.data_root}/runs/missing/outputs/records.json', success=False)
        self.output(); self.run_cli('synthetic-seal', 'T001', '--run', 'run1')
        self.run_cli('guard', 'edit', '--path', f'{self.data_root}/runs/run1/outputs/records.json', success=False)
