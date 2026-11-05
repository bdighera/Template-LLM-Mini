"""Knowledge retrieval: related memories, unified recall, citation checks and completion recovery."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from harness import HarnessTestCase
from test_governance import GovernanceTests


def crash_at_milestone(root, *command):
    """Run a command in a process that dies where it would append the milestone."""
    runner = ("import os, sys; sys.path.insert(0, 'scripts'); import workflow;"
              "workflow.milestone = lambda *a, **k: os._exit(9);"
              f"workflow.main({list(command)!r})")
    env = dict(os.environ, WORKFLOW_AGENT='claude')
    env.pop('WORKFLOW_ROLE', None)
    return subprocess.run([sys.executable, '-c', runner], cwd=root, env=env).returncode


class KnowledgeBase(HarnessTestCase):
    def decide(self, text, task=None, supersedes=None, rationale='Reason', outcome='Outcome', evidence=()):
        self.put('decision.json', dict(decision=text, rationale=rationale, alternatives=[], outcome=outcome,
                                       status='accepted', evidence=list(evidence)))
        args = ['decide', '--file', 'decision.json']
        args += ['--task', task] if task else []
        args += ['--supersedes', supersedes] if supersedes else []
        return self.run_cli(*args).stdout.strip()

    def finish_task(self, task='T001', memory='Behavior: example.\n'):
        self.handoff('reviewer', task=task); self.review(task=task); self.handoff('tester', task=task)
        self.run_cli('test', task)
        self.put('memory.md', memory)
        self.run_cli('memory', task, '--file', 'memory.md')
        self.handoff('coordinator', task=task)
        self.run_cli('done', task)

    def add_task(self, *links):
        return self.run_cli('add', '--title', 'Later work', '--criterion', 'Works', '--prompt', 'prompt.md',
                            '--path', 'example.py', *links).stdout.strip()

    def recall(self, query, *args):
        return json.loads(self.run_cli('recall', query, *args).stdout)

    def packet(self, task, role='builder', *args):
        return json.loads(self.run_cli('context', task, '--role', role, *args).stdout)


class RelatedMemoryTests(KnowledgeBase):
    def setUp(self):
        super().setUp()
        self.configure(); self.add(); self.run_cli('start', 'T001')
        self.finish_task()
        self.memory = self.task()['memories'][-1]

    def test_builder_and_coordinator_get_paths_but_not_text(self):
        self.put('memory.md', 'unused\n')
        self.add_task('--depends', 'T001'); self.run_cli('start', 'T002')
        expected = [dict(task='T001', memory=self.memory)]
        for role in ('builder', 'coordinator'):
            self.assertEqual(self.packet('T002', role)['related_memories'], expected)
        self.assertNotIn('Behavior: example', self.run_cli('context', 'T002', '--role', 'builder').stdout)

    def test_follows_links_count_and_are_not_duplicated(self):
        self.add_task('--depends', 'T001', '--follows', 'T001'); self.run_cli('start', 'T002')
        self.assertEqual(self.packet('T002')['related_memories'], [dict(task='T001', memory=self.memory)])

    def test_reviewer_and_unlinked_tasks_get_none(self):
        self.add_task(); self.run_cli('start', 'T002')
        self.assertNotIn('related_memories', self.packet('T002'))
        self.handoff('reviewer', task='T002')
        self.add_task('--depends', 'T001')
        self.assertNotIn('related_memories', self.packet('T003', 'reviewer'))

    def test_links_are_direct_only(self):
        self.add_task('--depends', 'T001'); self.run_cli('start', 'T002')
        self.finish_task('T002')
        self.add_task('--depends', 'T002'); self.run_cli('start', 'T003')
        self.assertEqual([item['task'] for item in self.packet('T003')['related_memories']], ['T002'])

    def test_links_without_memory_are_skipped(self):
        self.add_task(); self.add_task('--depends', 'T002')
        self.assertNotIn('related_memories', self.packet('T003'))

    def test_cap_keeps_newest_and_reports_the_rest(self):
        for number in range(2, 8):
            task = self.add_task(); self.run_cli('start', task); self.finish_task(task)
        links = [arg for number in range(1, 8) for arg in ('--depends', f'T{number:03}')]
        self.add_task(*links)
        packet = self.packet('T008')
        self.assertEqual([item['task'] for item in packet['related_memories']], ['T007', 'T006', 'T005', 'T004', 'T003'])
        self.assertIn('related_memories: 2 older', packet['omitted'])

    def test_size_limit_drops_it_after_other_optional_sections(self):
        # Padding keeps the core above the CLI's 2000-character minimum limit.
        self.run_cli('add', '--title', 'Later work', '--criterion', 'x' * 2500, '--prompt', 'prompt.md',
                     '--path', 'example.py', '--depends', 'T001')
        self.run_cli('start', 'T002')
        output = self.run_cli('context', 'T002', '--role', 'builder').stdout
        dropped_before = 0
        while True:  # Each limit is just under the last packet, forcing one more section out.
            output = self.run_cli('context', 'T002', '--role', 'builder', '--max-chars', str(len(output) - 2)).stdout
            packet = json.loads(output)
            if 'related_memories' not in packet:
                break
            self.assertGreater(len(packet['omitted']), dropped_before)
            dropped_before = len(packet['omitted'])
        self.assertGreater(dropped_before, 0)
        self.assertEqual(packet['omitted'][-1], 'related_memories')


class UnifiedRecallTests(KnowledgeBase):
    def setUp(self):
        super().setUp()
        self.configure(); self.add(); self.run_cli('start', 'T001')

    def test_finds_decisions_with_kind_and_doubles_decision_text(self):
        first = self.decide('Use caching for parsers', task='T001')
        second = self.decide('Unrelated choice', rationale='Mentions caching once')
        hits = self.recall('caching')
        self.assertEqual([(h['kind'], h['id'], h['score']) for h in hits], [('decision', first, 2), ('decision', second, 1)])
        self.assertEqual(hits[0]['task'], 'T001')
        self.assertEqual(hits[0]['status'], 'accepted')
        self.assertIsNone(hits[1]['task'])

    def test_memory_hits_keep_their_fields_and_gain_kind(self):
        self.finish_task(memory='Behavior: parser output.\n')
        self.assertEqual(self.recall('parser'), [dict(kind='memory', task='T001', title='Example',
                         memory=self.task()['memories'][-1], score=1)])

    def test_milestones_are_not_searched(self):
        self.assertEqual(self.recall('planned'), [])
        self.assertIn('Task planned', (self.root / 'logging/journal.jsonl').read_text())

    def test_long_decision_text_is_clipped(self):
        self.decide('caching ' + 'x' * 500)
        text = self.recall('caching')[0]['decision']
        self.assertEqual(len(text), 160)
        self.assertTrue(text.endswith('…'))

    def test_limit_spans_both_kinds(self):
        self.decide('caching one'); self.decide('caching two')
        self.finish_task(memory='caching memory\n')
        self.assertEqual(len(self.recall('caching')), 3)
        self.assertEqual(len(self.recall('caching', '--limit', '2')), 2)

    def test_superseded_decision_is_flagged_and_loses_ties(self):
        old = self.decide('Use caching')
        new = self.decide('Use caching differently', supersedes=old)
        hits = {h['id']: h for h in self.recall('caching')}
        self.assertEqual(hits[old]['superseded_by'], new)
        self.assertNotIn('superseded_by', hits[new])
        self.assertEqual(self.recall('use')[0]['id'], new)

    def test_memory_citing_superseded_decision_is_flagged(self):
        old = self.decide('Use redis')
        self.finish_task(memory=f'Behavior: redis store ({old}).\n')
        hit = self.recall('store')[0]
        self.assertNotIn('cites_superseded', hit)
        new = self.decide('Use sqlite', supersedes=old)
        self.assertEqual(self.recall('store')[0]['cites_superseded'], [dict(id=old, superseded_by=new)])

    def test_corrupt_journal_warns_and_keeps_memories(self):
        self.finish_task(memory='Behavior: parser output.\n')
        path = self.root / 'logging/journal.jsonl'
        path.write_text(path.read_text() + '{"partial"')
        result = self.run_cli('recall', 'parser')
        self.assertEqual([h['kind'] for h in json.loads(result.stdout)], ['memory'])
        self.assertIn('journal not searched', result.stderr)


class CitationTests(KnowledgeBase):
    def setUp(self):
        super().setUp()
        self.configure(); self.add(); self.run_cli('start', 'T001')

    def handoff_with(self, **values):
        self.put('handoff.json', dict(dict(objective='Works', files=['example.py'], memories=[], decisions='Small scope',
                 changes='Example', validation='See checks', unresolved='none', next_action='Review'), **values))

    def test_memory_rejects_unknown_and_accepts_known(self):
        known = self.decide('A decision')
        self.handoff('reviewer'); self.review(); self.handoff('tester'); self.run_cli('test', 'T001')
        self.put('memory.md', 'Decisions: J999999.\n')
        self.assertIn('Unknown journal IDs: J999999', self.run_cli('memory', 'T001', '--file', 'memory.md', success=False).stderr)
        self.assertEqual(self.task()['memories'], [])
        self.put('memory.md', f'Decisions: {known}.\n')
        self.run_cli('memory', 'T001', '--file', 'memory.md')

    def test_handoff_checks_every_text_field(self):
        for field in ('decisions', 'changes', 'unresolved', 'next_action'):
            self.handoff_with(**{field: 'See J999999'})
            self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        self.assertEqual(self.task()['status'], 'building')

    def test_handoff_set_override_is_checked(self):
        self.handoff('reviewer'); self.review()
        self.run_cli('handoff', 'T001', '--to', 'tester', '--set', 'decisions=See J999999', success=False)

    def test_reused_handoff_error_names_its_source(self):
        self.handoff('reviewer'); self.review()
        ref = self.task()['handoffs'][-1]
        path = self.root / ref
        content = json.loads(path.read_text())
        content['decisions'] = 'An older record citing J999999'
        path.write_text(json.dumps(content))
        state = self.state()
        state['tasks'][0]['artifacts'][ref] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root / '.workflow/state.json').write_text(json.dumps(state))
        error = self.run_cli('handoff', 'T001', '--to', 'tester', success=False).stderr
        self.assertIn(f'reused from {ref}; override with --set', error)
        self.run_cli('handoff', 'T001', '--to', 'tester', '--set', 'decisions=Corrected')

    def test_explicit_handoff_error_has_no_reuse_note(self):
        self.handoff_with(decisions='See J999999')
        error = self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False).stderr
        self.assertNotIn('reused from', error)

    def test_checkpoint_is_checked(self):
        self.handoff_with(decisions='See J999999')
        self.run_cli('checkpoint', 'T001', '--file', 'handoff.json', success=False)

    def test_superseded_citation_is_allowed(self):
        old = self.decide('A decision')
        self.decide('Another', supersedes=old)
        self.handoff_with(decisions=f'History: {old}')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json')

    def test_near_matches_are_ignored(self):
        self.handoff_with(decisions='TJ999999, J99999 and J999999x are not IDs')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json')

    def test_existing_records_with_unknown_ids_do_not_fail_check(self):
        self.handoff('reviewer'); self.review(); self.handoff('tester'); self.run_cli('test', 'T001')
        self.run_cli('memory', 'T001', '--file', 'memory.md')
        self.handoff('coordinator'); self.run_cli('done', 'T001')
        # An older project's completed memory may contain an ID that was never checked.
        task = self.task()
        ref = task['memories'][-1]
        path = self.root / ref
        content = json.loads(path.read_text())
        content['report'] = 'Decisions: J999999.\n'
        path.write_text(json.dumps(content))
        state = self.state()
        state['tasks'][0]['artifacts'][ref] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root / '.workflow/state.json').write_text(json.dumps(state))
        self.run_cli('check')
        self.assertEqual(self.recall('decisions')[0]['task'], 'T001')


class ReconcileTests(KnowledgeBase):
    def setUp(self):
        super().setUp()
        self.configure(); self.add(); self.run_cli('start', 'T001')
        self.handoff('reviewer'); self.review(); self.handoff('tester')
        self.run_cli('test', 'T001'); self.run_cli('memory', 'T001', '--file', 'memory.md')
        self.handoff('coordinator')

    def completions(self):
        entries = json.loads(self.run_cli('journal', '--task', 'T001', '--all', '--full', '--max-chars', '100000').stdout)['entries']
        return [e for e in entries if e['decision'].startswith('Task completed: ')]

    def test_crash_is_detected_repaired_and_not_repeated(self):
        self.assertEqual(crash_at_milestone(self.root, 'done', 'T001'), 9)
        self.assertEqual(self.task()['status'], 'done')
        self.assertEqual(self.completions(), [])
        self.assertIn('run workflow.py reconcile T001', self.run_cli('check', success=False).stderr)
        before = (self.root / '.workflow/state.json').read_bytes()
        self.assertEqual(self.run_cli('reconcile', 'T001').stdout.strip(), self.completions()[0]['id'])
        self.assertEqual((self.root / '.workflow/state.json').read_bytes(), before)
        entry = self.completions()[0]
        self.assertTrue(entry['rationale'].startswith('Recovered:'))
        self.assertEqual(entry['evidence'], [self.task()[k][-1] for k in ('reviews', 'tests', 'memories', 'handoffs')])
        self.run_cli('check')
        self.assertIn('already has', self.run_cli('reconcile', 'T001', success=False).stderr)
        self.assertEqual(len(self.completions()), 1)

    def test_normal_completion_needs_no_repair(self):
        self.run_cli('done', 'T001')
        self.run_cli('check')
        self.assertIn('already has', self.run_cli('reconcile', 'T001', success=False).stderr)

    def test_unfinished_task_is_refused(self):
        self.assertIn('Only done tasks', self.run_cli('reconcile', 'T001', success=False).stderr)
        self.assertEqual(self.completions(), [])

    def test_unknown_task_is_refused(self):
        self.run_cli('reconcile', 'T999', success=False)


class MaintenanceReconcileTests(GovernanceTests):
    def test_maintenance_crash_uses_its_own_milestone(self):
        self.setup_bounded('User approved narrow maintenance policy')
        self.run_cli('add', '--title', 'Correction', '--criterion', 'Works', '--prompt-text', 'Correct typo',
                     '--path', 'example.py', '--class', 'maintenance')
        self.run_cli('start', 'T001')
        self.put('maintenance.json', dict(changes='Corrected typo', verification=[], verification_reason='Read the text', handoff='Done'))
        payload = str(Path(self.temp.name) / 'maintenance.json')
        self.assertEqual(crash_at_milestone(self.root, 'maintenance-finish', 'T001', '--file', payload), 9)
        self.assertIn('reconcile T001', self.run_cli('check', success=False).stderr)
        self.run_cli('reconcile', 'T001')
        self.run_cli('check')
        entries = json.loads(self.run_cli('journal', '--task', 'T001').stdout)['entries']
        self.assertEqual(entries[-1]['decision'], 'Maintenance completed: Correction')


# Inherited tests already run from test_governance; collect only the additions here.
def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for case in (RelatedMemoryTests, UnifiedRecallTests, CitationTests, ReconcileTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    suite.addTest(MaintenanceReconcileTests('test_maintenance_crash_uses_its_own_milestone'))
    return suite


if __name__ == '__main__':
    unittest.main()
