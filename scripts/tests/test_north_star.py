"""Behavioral tests for checkpoint gates and explicit, committed publication."""
import json
from harness import HarnessTestCase


class NorthStarTests(HarnessTestCase):
    def seed(self, count, significant=False):
        state = self.state()
        state['tasks'] = [dict(id=f'T{i:03}', title='Historical', criteria=['Works'], status='done', significant=significant, paths=[], dependencies=[], repairs=0, prompts=[], analysis_state={}, follows=None, **{k: [] for k in ('handoffs','reviews','tests','memories')}) for i in range(1, count + 1)]
        self.put('.workflow/state.json', state)

    def info(self):
        return json.loads(self.run_cli('north-star', 'status').stdout)

    def draft(self):
        self.put('vision.md', '# North Star\n\n' + '\n\n'.join(
            heading + '\nRepository-backed assessment.' for heading in (
                '## Core vision', '## Guiding principles', '## Worth building',
                '## Exclusions', '## Drift assessment', '## Repository evidence')) + '\n')
        return json.loads(self.run_cli('north-star', 'draft', '--file', 'vision.md').stdout)['preview_hash']

    def publish(self, token, success=True):
        return self.run_cli('north-star', 'publish', '--approved', '--preview-hash', token, success=success)

    def test_thresholds_and_adoption(self):
        self.configure()
        for count, pending in ((0, False), (9, False), (10, True), (19, True), (20, True), (37, True)):
            self.seed(count)
            self.assertEqual(self.info()['pending'], pending)

    def test_overdue_blocks_start_and_creates_pending_draft(self):
        self.configure(); self.seed(10)
        task = self.add()
        self.run_cli('start', task, success=False)
        self.run_cli('claim', task, success=False)
        self.assertTrue((self.root / '.workflow/NORTH_STAR.draft.md').exists())
        self.assertFalse((self.root / 'NORTH_STAR.md').exists())

    def test_significant_default_requires_assessment(self):
        self.configure()
        self.run_cli('add', '--title', 'Capability', '--criterion', 'Works', '--prompt-text', 'New feature', '--path', 'example.py')
        self.run_cli('start', 'T001', success=False)
        self.put('alignment.md', 'Aligned with PROJECT.md purpose; no conflict.')
        self.run_cli('north-star', 'assess', '--task', 'T001', '--file', 'alignment.md')
        self.run_cli('start', 'T001')

    def test_significant_completion_overlaps_periodic_gate(self):
        self.configure(); self.seed(10, significant=True)
        self.assertEqual(len(self.info()['reasons']), 2)

    def test_publication_approval_commit_and_next_checkpoint(self):
        self.configure(); self.git_init(); self.seed(10)
        self.run_cli('install-hooks')
        self.git('add', 'example.py')
        token = self.draft()
        self.run_cli('north-star', 'publish', '--preview-hash', token, success=False)
        self.publish(token)
        self.assertFalse(self.info()['pending'])
        self.assertIn('example.py', self.git('diff', '--cached', '--name-only').stdout)
        self.assertEqual(set(self.git('show', '--pretty=', '--name-only', 'HEAD').stdout.split()),
                         {'NORTH_STAR.md', '.workflow/north-star-approval.json', '.workflow/north-star.json'})
        self.seed(19); self.assertFalse(self.info()['pending'])
        self.seed(20); self.assertTrue(self.info()['pending'])
        original = (self.root / 'NORTH_STAR.md').read_text()
        self.draft()
        self.assertEqual((self.root / 'NORTH_STAR.md').read_text(), original)

    def test_stale_draft_and_repository_approval(self):
        self.configure(); self.git_init(); self.seed(10)
        token = self.draft()
        self.put('scripts/new_source.py', '# changed source\n')
        self.publish(token, success=False)
        token = self.draft()
        with (self.root / '.workflow/NORTH_STAR.draft.md').open('a') as stream:
            stream.write('Changed after approval\n')
        self.publish(token, success=False)

    def test_commit_failure_retry(self):
        self.configure(); self.git_init(); self.seed(10)
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        token = self.draft(); self.publish(token, success=False)
        self.assertTrue(self.info()['pending'])
        hook.write_text('#!/bin/sh\nexit 0\n')
        self.publish(token)
        self.assertFalse(self.info()['pending'])
        self.publish(token)  # idempotent recovery

    def test_external_file_detection_and_guard(self):
        self.configure(); self.put('NORTH_STAR.md', 'Unapproved')
        self.assertTrue(self.info()['pending'])
        self.run_cli('guard', 'edit', '--path', 'NORTH_STAR.md', code=2)

    def test_real_tenth_completion_and_existing_lease(self):
        self.prepare_testing()
        state = self.state()
        import copy
        for i in range(2, 11):
            task = copy.deepcopy(state['tasks'][0])
            task.update(id=f'T{i:03}', status='done', claim=None)
            state['tasks'].append(task)
        self.put('.workflow/state.json', state)
        self.finish()
        self.assertTrue(self.info()['pending'])
        self.assertTrue((self.root / '.workflow/NORTH_STAR.draft.md').exists())

    def test_real_significant_completion(self):
        self.prepare_testing()
        state = self.state(); state['tasks'][0]['significant'] = True
        self.put('.workflow/state.json', state)
        self.finish()
        self.assertIn('significant feature completed', self.info()['reasons'])

    def test_pending_allows_existing_owner_heartbeat(self):
        self.configure(); self.add(); self.run_cli('start', 'T001')
        self.put('NORTH_STAR.md', 'Detected unapproved vision')
        self.run_cli('heartbeat', 'T001')
        self.run_cli('claim', 'T001')
        self.assertTrue(self.info()['pending'])

    def test_committed_metadata_survives_clean_clone(self):
        import subprocess
        self.configure(); self.git_init(); self.seed(10)
        self.git('add', '.')
        self.git('commit', '-m', 'Configured fixture')
        self.publish(self.draft())
        clone = self.root.parent / 'clone'
        subprocess.run(['git', 'clone', '-q', str(self.root), str(clone)], check=True)
        self.root = clone
        self.assertFalse(self.info()['pending'])

    def test_documentation_and_previous_vision_changes_stale_approval(self):
        self.configure(); self.git_init(); self.seed(10)
        self.publish(self.draft())
        token = self.draft()
        self.put('README.md', 'Changed repository evidence')
        self.publish(token, success=False)
        token = self.draft()
        self.put('NORTH_STAR.md', 'Changed authoritative vision')
        self.publish(token, success=False)

    def test_assess_respects_foreign_claim(self):
        self.configure(); self.add(); self.run_cli('claim', 'T001', agent='claude')
        self.put('alignment.md', 'Aligned')
        self.run_cli('north-star', 'assess', '--task', 'T001', '--file', 'alignment.md', agent='codex', success=False)
