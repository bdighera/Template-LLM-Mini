"""Core workflow behavior in isolated template copies; no provider calls."""
import json
import subprocess
import sys
import unittest

from harness import DATA_ADDON, HarnessTestCase


class WorkflowTests(HarnessTestCase):
    def test_full_feature_and_completion_gates(self):
        self.configure(); self.add()
        self.run_cli('done', 'T001', success=False)
        self.run_cli('start', 'T001'); self.handoff('reviewer')
        self.run_cli('test', 'T001', success=False)
        self.review()
        self.handoff('tester'); self.run_cli('test', 'T001')
        self.run_cli('done', 'T001', success=False)
        self.run_cli('memory', 'T001', '--file', 'memory.md')
        self.run_cli('done', 'T001', success=False)
        self.handoff('coordinator'); self.run_cli('done', 'T001'); self.run_cli('check')
        self.assertEqual(self.task()['status'], 'done')
        self.assertEqual(len(self.task()['prompts']), 5)
        self.assertIn('T001', (self.root / 'memories/index.md').read_text())
        self.assertIn('Sample', (self.root / 'PROJECT.md').read_text())
        self.assertNotIn('Public synthetic data', (self.root / 'AGENTS.md').read_text())
        self.assertIn('Python', (self.root / 'AGENTS.md').read_text())
        self.assertIn('data/generated/', (self.root / 'AGENTS.md').read_text())
        self.run_cli('start', 'T001', success=False)

    def test_rejection_repair_limit_and_dependencies(self):
        self.config['handoffs']['max_repairs'] = 0
        self.put('config.json', self.config); self.configure(); self.add(); self.add('T001')
        self.run_cli('start', 'T002', success=False)
        self.run_cli('start', 'T001'); self.handoff('reviewer')
        self.review('fail')
        self.assertEqual(self.task()['status'], 'blocked')
        self.run_cli('unblock', 'T001', success=False)

    def test_failed_validation_invalidates_review(self):
        self.config['validation']['commands'] = [[sys.executable, '-c', 'raise SystemExit(3)']]
        self.put('config.json', self.config); self.configure(); self.add()
        self.run_cli('start', 'T001'); self.handoff('reviewer')
        self.review()
        self.handoff('tester'); self.run_cli('test', 'T001', success=False)
        self.assertEqual(self.task()['status'], 'building')
        self.assertFalse(self.task()['review_pass'])
        self.run_cli('memory', 'T001', '--file', 'memory.md', success=False)

    def test_approval_and_configuration_validation(self):
        self.run_cli('configure', '--config', 'config.json', success=False)
        self.config['validation']['commands'] = []
        self.put('config.json', self.config)
        self.run_cli('configure', '--config', 'config.json', '--approved', success=False)
        self.assertFalse((self.root / '.workflow/config.json').exists())

    def test_missing_evidence_is_detected(self):
        self.configure(); self.add()
        (self.root / self.task()['prompts'][0]).unlink()
        self.run_cli('check', success=False)

    def test_malformed_handoff_does_not_advance_task(self):
        self.configure(); self.add(); self.run_cli('start', 'T001')
        self.put('bad.json', {'objective': 'Missing remaining fields'})
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'bad.json', success=False)
        self.assertEqual(self.task()['status'], 'building')

    def test_data_configuration_requires_answers_and_preserves_policies(self):
        self.config['data']['sources'] = ''
        self.put('config.json', self.config)
        self.run_cli('configure', '--config', 'config.json', '--approved', success=False)
        self.config['data']['sources'] = 'none'
        self.config['data']['raw_policy'] = 'Overwrite originals'
        self.put('config.json', self.config)
        self.run_cli('configure', '--config', 'config.json', '--approved', success=False)
        self.assertFalse((self.root / '.workflow/config.json').exists())

    def test_git_ignores_datasets_but_preserves_layout(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True, capture_output=True)
        for folder in ('raw', 'processed', 'generated'):
            base = self.root / 'data' / folder
            self.assertTrue((base / '.gitkeep').is_file())
            (base / 'nested').mkdir()
            for name in ('records.csv', 'nested/records.json'):
                (base / name).write_text('sample')
                result = subprocess.run(['git', 'check-ignore', '--no-index', '-q',
                                         f'data/{folder}/{name}'], cwd=self.root)
                self.assertEqual(result.returncode, 0)
            result = subprocess.run(['git', 'check-ignore', '--no-index', '-q',
                                     f'data/{folder}/.gitkeep'], cwd=self.root)
            self.assertEqual(result.returncode, 1)

    def test_source_changes_invalidate_passes(self):
        self.prepare_testing(); self.run_cli('test', 'T001')
        self.put('example.py', 'raise RuntimeError("changed")\n')
        self.run_cli('memory', 'T001', '--file', 'memory.md', success=False)
        self.assertEqual(self.task()['status'], 'building')
        self.assertFalse(self.task()['review_pass'])
        self.assertFalse(self.task()['test_pass'])

    def test_added_source_file_invalidates_review(self):
        self.prepare_testing()
        self.put('scripts/new_feature.py', 'print("new")\n')
        self.run_cli('test', 'T001', success=False)
        self.assertFalse(self.task()['review_pass'])

    def test_completion_rejects_missing_memory(self):
        self.prepare_testing(); self.run_cli('test', 'T001')
        self.run_cli('memory', 'T001', '--file', 'memory.md'); self.handoff('coordinator')
        (self.root / self.task()['memories'][-1]).unlink()
        self.run_cli('done', 'T001', success=False)
        self.run_cli('check', success=False)

    def test_tampered_evidence_rejected(self):
        self.prepare_testing(); self.run_cli('test', 'T001')
        record = self.root / self.task()['reviews'][-1]
        record.write_text(record.read_text().replace('"pass"', '"fail"'))
        self.run_cli('memory', 'T001', '--file', 'memory.md', success=False)
        self.run_cli('check', success=False)

    def test_later_source_changes_preserve_completed_history(self):
        self.prepare_testing(); self.finish()
        self.put('example.py', 'print("next feature")\n')
        self.run_cli('check')
        self.assertEqual(self.task()['status'], 'done')

    def test_checkpoint_resume_and_stale_warning(self):
        self.configure(); self.add(); self.run_cli('start', 'T001')
        before = self.task()['status']
        self.run_cli('checkpoint', 'T001', '--file', 'handoff.json')
        resumed = json.loads(self.run_cli('resume', 'T001', '--role', 'builder').stdout)
        self.assertEqual(before, self.task()['status'])
        self.assertFalse(resumed['checkpoint_stale'])
        self.assertEqual(resumed['checkpoints']['content']['next_action'], 'Evaluate acceptance criteria')
        self.put('example.py', 'print("later change")\n')
        resumed = json.loads(self.run_cli('resume', 'T001').stdout)
        self.assertTrue(resumed['checkpoint_stale'])

    def test_context_budget_reports_omissions(self):
        self.prepare_testing()
        h = json.loads((self.root / 'handoff.json').read_text())
        h['changes'] = 'large detail ' * 2000
        self.put('handoff.json', h)
        self.run_cli('checkpoint', 'T001', '--file', 'handoff.json')
        r = self.run_cli('context', 'T001', '--role', 'tester', '--max-chars', '2000')
        packet = json.loads(r.stdout)
        self.assertLessEqual(len(r.stdout.strip()), 2000)
        self.assertTrue(packet['omitted'])
        self.assertEqual(packet['criteria'], ['Works'])
        self.assertIn('checkpoints', packet['latest'])

    def test_status_and_show_are_focused(self):
        self.prepare_testing(); self.finish(); self.add()
        active = json.loads(self.run_cli('status').stdout)
        self.assertEqual([x['id'] for x in active], ['T002'])
        all_tasks = json.loads(self.run_cli('status', '--all').stdout)
        self.assertEqual(len(all_tasks), 2)
        view = json.loads(self.run_cli('show', 'T001').stdout)
        self.assertNotIn('artifacts', view)
        self.assertEqual(view['status'], 'done')

    def test_recall_ranks_memories_by_query_words(self):
        self.prepare_testing(); self.finish()
        self.add(); self.run_cli('start', 'T002')
        self.handoff('reviewer', task='T002'); self.review(task='T002'); self.handoff('tester', task='T002')
        self.run_cli('test', 'T002')
        self.put('memory.md', 'Behavior: parser output cache. Validation: parser cache tests.\n')
        self.run_cli('memory', 'T002', '--file', 'memory.md')
        hits = json.loads(self.run_cli('recall', 'Parser cache').stdout)
        self.assertEqual([hit['task'] for hit in hits], ['T002'])
        self.assertTrue(hits[0]['memory'].startswith('memories/features/T002-'))
        # T002 (later) outranks T001; T001 scores 2 for its title + 1 for its report.
        ranked = json.loads(self.run_cli('recall', 'parser cache example').stdout)
        self.assertEqual([(hit['task'], hit['score']) for hit in ranked], [('T002', 6), ('T001', 3)])
        self.assertEqual([hit['task'] for hit in json.loads(self.run_cli('recall', 'example', '--limit', '1').stdout)], ['T001'])
        self.assertEqual(json.loads(self.run_cli('recall', 'zebra').stdout), [])
        self.run_cli('recall', '...', success=False)

    def test_assignment_logs_reference_handoffs_without_copying(self):
        self.prepare_testing()
        logged = (self.root / self.task()['prompts'][-1]).read_text()
        self.assertIn(self.task()['handoffs'][-1], logged)
        self.assertNotIn('"acceptance_criteria"', logged)
        self.assertIn('agents/tester.md', logged)

    def test_strict_config_types_and_source_exclusions(self):
        self.config['project']['purpose'] = 7
        self.put('config.json', self.config)
        self.run_cli('configure', '--config', 'config.json', '--approved', success=False)
        self.config['project']['purpose'] = 'Sample'
        self.config['validation']['source_paths'] = ['data/raw']
        self.put('config.json', self.config)
        self.run_cli('configure', '--config', 'config.json', '--approved', success=False)

    def test_data_changes_do_not_invalidate_sources(self):
        self.prepare_testing()
        self.put('data/raw/input.csv', 'unrelated data\n')
        self.run_cli('test', 'T001')
        self.assertTrue(self.task()['test_pass'])

    def test_summary_diagnostics_are_explicit_and_bounded(self):
        self.config['validation'].update(diagnostics='summary', summary_max_chars=5, timeout_seconds=120)
        self.config['validation']['commands'] = [[sys.executable, '-c',
            'import json; print(json.dumps({"summary": "safe diagnostic"})); raise SystemExit(3)']]
        self.put('config.json', self.config); self.prepare_testing()
        self.run_cli('test', 'T001', success=False)
        record = json.loads((self.root / self.task()['tests'][-1]).read_text())
        self.assertEqual(record['results'][0]['summary'], 'safe ')
        self.assertTrue(record['results'][0]['summary_truncated'])

    def test_raw_diagnostics_are_not_saved(self):
        self.config['validation']['commands'] = [[sys.executable, '-c',
            'print("PRIVATE-" + str(42)); raise SystemExit(3)']]
        self.put('config.json', self.config); self.prepare_testing()
        self.run_cli('test', 'T001', success=False)
        content = (self.root / self.task()['tests'][-1]).read_text()
        self.assertNotIn('PRIVATE-42', content)

    def test_checks_that_change_source_invalidate_their_results(self):
        self.config['validation']['commands'] = [[sys.executable, '-c',
            'from pathlib import Path; Path("example.py").write_text("changed")']]
        self.put('config.json', self.config); self.prepare_testing()
        self.run_cli('test', 'T001', success=False)
        self.assertFalse(self.task()['test_pass'])
        self.assertEqual(self.task()['status'], 'building')

    def test_setup_works_from_configured_adoption_without_copying_data(self):
        self.configure()
        self.put('data/raw/private.csv', 'private data')
        (self.root / '.venv').mkdir()
        self.put('.venv/large.bin', 'unwanted environment')
        import harness
        names = harness.harness_files(self.root)
        self.assertFalse([n for n in names if n.startswith(('data/raw/private', '.venv', '.workflow'))])

    def test_configuration_preflight_and_preview_do_not_mutate_state(self):
        self.configure(); self.add()
        before = (self.root / '.workflow/state.json').read_bytes()
        self.run_cli('validate-config', '--config', 'config.json')
        self.config['context']['max_chars'] = 9000
        self.put('candidate.json', self.config)
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        self.assertIn('context.max_chars', [item['path'] for item in preview['changes']])
        self.assertEqual(before, (self.root / '.workflow/state.json').read_bytes())
        self.assertFalse((self.root / '.workflow/config-revisions').exists())
        self.run_cli('update-config', '--config', 'candidate.json', '--approved', success=False)

    def test_config_update_rejects_stale_preview_and_preserves_history(self):
        self.prepare_testing(); self.finish(); self.add()
        self.config['context']['max_chars'] = 9000
        self.put('candidate.json', self.config)
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        self.run_cli('log', 'T002', '--file', 'prompt.md')
        self.run_cli('update-config', '--config', 'candidate.json', '--approved',
                     '--preview-hash', preview['preview_hash'], success=False)
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        old_artifacts = dict(self.task()['artifacts'])
        self.run_cli('update-config', '--config', 'candidate.json', '--approved', '--preview-hash', preview['preview_hash'])
        self.assertEqual(self.task()['status'], 'done')
        self.assertEqual(self.task()['artifacts'], old_artifacts)
        self.run_cli('check')
        revision = next((self.root / '.workflow/config-revisions').glob('*.json'))
        saved = json.loads(revision.read_text())
        self.assertEqual(saved['status'], 'applied')
        self.assertEqual(len(saved['previous_state']['tasks']), 2)

    def test_guidance_edits_are_in_preview_and_invalidate_old_approval(self):
        self.configure()
        (self.root / 'AGENTS.md').write_text((self.root / 'AGENTS.md').read_text() + '\nCustom project rule.\n')
        preview = json.loads(self.run_cli('update-config', '--config', 'config.json').stdout)
        self.assertIn('Custom project rule.', preview['guidance_changes']['AGENTS.md'])
        (self.root / 'PROJECT.md').write_text('Changed after preview\n')
        self.run_cli('update-config', '--config', 'config.json', '--approved', '--preview-hash', preview['preview_hash'], success=False)

    def decision_payload(self, decision='Use Python for deterministic work'):
        payload = json.loads((self.root / 'templates/decision.json').read_text())
        payload.update(decision=decision, rationale='Avoid unnecessary model calls.',
                       outcome='Python selected; validation tracked separately.', evidence=['prompt.md'])
        self.put('decision.json', payload)

    def test_project_decision_before_config_and_automatic_milestones(self):
        self.decision_payload()
        first = self.run_cli('decide', '--file', 'decision.json').stdout.strip()
        prefix = (self.root / 'logging/journal.jsonl').read_bytes()
        self.prepare_testing(); self.finish()
        self.assertTrue((self.root / 'logging/journal.jsonl').read_bytes().startswith(prefix))
        records = json.loads(self.run_cli('journal', '--all').stdout)['entries']
        self.assertEqual(records[0]['id'], first)
        milestones = [record for record in records if record['type'] == 'milestone']
        self.assertEqual(len(milestones), 3)  # Configure, add, done.
        self.assertEqual([record['task'] for record in milestones], [None, 'T001', 'T001'])
        task_records = json.loads(self.run_cli('journal', '--task', 'T001').stdout)['entries']
        self.assertEqual(len(task_records), 2)

    def test_decisions_do_not_invalidate_validation_and_unknown_tasks_rejected(self):
        self.prepare_testing(); self.run_cli('test', 'T001')
        self.decision_payload()
        self.run_cli('decide', '--task', 'T999', '--file', 'decision.json', success=False)
        self.run_cli('decide', '--task', 'T001', '--file', 'decision.json')
        self.run_cli('memory', 'T001', '--file', 'memory.md')
        self.handoff('coordinator'); self.run_cli('done', 'T001'); self.run_cli('check')
        self.assertEqual(self.task()['status'], 'done')

    def test_config_update_adds_one_milestone_only_after_apply(self):
        self.configure()
        self.config['context']['max_chars'] = 9000
        self.put('candidate.json', self.config)
        before = (self.root / 'logging/journal.jsonl').read_bytes()
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        self.assertEqual(before, (self.root / 'logging/journal.jsonl').read_bytes())
        self.run_cli('update-config', '--config', 'candidate.json', '--approved', '--preview-hash', preview['preview_hash'])
        records = json.loads(self.run_cli('journal', '--type', 'milestone', '--all', '--full').stdout)['entries']
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]['decision'], 'Configuration update applied')
        self.assertIn('config-revisions', records[-1]['evidence'][0])

    def test_journal_cli_defaults_compact_and_full_is_explicit(self):
        self.decision_payload('Long original decision ' + 'details ' * 100)
        self.run_cli('decide', '--file', 'decision.json')
        output = self.run_cli('journal', '--max-chars', '1500').stdout
        self.assertLessEqual(len(output), 1500)
        result = json.loads(output)
        self.assertEqual(result['mode'], 'compact')
        self.assertIn('decision', result['entries'][0]['truncated_fields'])
        full = json.loads(self.run_cli('journal', '--id', result['entries'][0]['id'], '--full').stdout)
        self.assertEqual(full['mode'], 'full')
        self.assertIn('evidence', full['entries'][0])
        self.assertGreater(len(full['entries'][0]['decision']), 160)


    def test_messages_name_the_fix(self):
        self.config['validation']['commands'] = ['python3 -m unittest']
        self.put('config.json', self.config)
        result = self.run_cli('validate-config', '--config', 'config.json', success=False)
        self.assertIn('[["python3", "-m", "unittest"', result.stderr)
        self.config['validation']['commands'] = [[sys.executable, '-c', 'pass']]
        self.put('config.json', self.config)
        self.configure(); self.add(); self.run_cli('start', 'T001')
        result = self.run_cli('start', 'T001', success=False)
        self.assertIn('T001 is already building', result.stderr)
        self.put('handoff.json', dict(json.loads((self.root / 'handoff.json').read_text()), memories=['T001 memory']))
        result = self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', success=False)
        self.assertIn('existing project-relative file paths', result.stderr)

    def test_assumed_defaults_are_journaled_at_configure(self):
        self.put('assumed.md', '- users: data managers\n- add-on: enabled\n')
        self.run_cli('configure', '--config', 'config.json', '--approved', '--assumed', 'assumed.md')
        entry = json.loads((self.root / 'logging/journal.jsonl').read_text().splitlines()[0])
        self.assertIn('Assumed defaults: - users: data managers - add-on: enabled', entry['outcome'])


    def test_validate_config_lists_every_structural_problem(self):
        del self.config['technology']
        self.config['delegation']['max_concurrency'] = '2'
        self.config['project']['name'] = ''
        self.put('config.json', self.config)
        result = self.run_cli('validate-config', '--config', 'config.json', success=False)
        self.assertIn('3 problem(s)', result.stderr)
        for expected in ('config: missing keys technology', 'config.delegation.max_concurrency: expected integer',
                         'config.project.name: provide a nonempty string'):
            self.assertIn(expected, result.stderr)

    def test_approved_fixtures_are_unignored_and_the_block_is_enforced(self):
        self.git_init()
        self.put('data/raw/sample.csv', 'id\n1\n')
        self.config['data']['approved_fixtures'] = ['data/raw/sample.csv']
        self.put('config.json', self.config)
        self.configure()
        self.assertIn('!/data/raw/sample.csv', (self.root / '.gitignore').read_text())
        self.assertEqual(self.git('check-ignore', '-q', 'data/raw/sample.csv', check=False).returncode, 1)
        self.git('add', '.gitignore', 'data/raw/sample.csv')
        self.run_cli('guard', 'commit')
        self.put('.gitignore', (self.root / '.gitignore').read_text().replace('!/data/raw/sample.csv\n', ''))
        self.git('add', '.gitignore')
        self.assertIn('approved_fixtures block', self.run_cli('guard', 'commit', code=1).stderr)
        self.run_cli('install-hooks')
        self.assertIn('!/data/raw/sample.csv', (self.root / '.gitignore').read_text())
        self.config['data']['approved_fixtures'] = []
        self.put('candidate.json', self.config)
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        self.run_cli('update-config', '--config', 'candidate.json', '--approved', '--preview-hash', preview['preview_hash'])
        self.assertNotIn('approved_fixtures', (self.root / '.gitignore').read_text())

    def test_doctor_reports_setup_problems(self):
        self.configure()
        result = self.run_cli('doctor', agent=None, code=2)
        self.assertIn('warn: WORKFLOW_AGENT is unset', result.stdout)
        self.assertIn('warn: not a Git work tree', result.stdout)
        self.git_init(); self.run_cli('install-hooks')
        template = self.root.parent / 'template'
        template.mkdir(); (template / 'AGENTS.md').write_text('# Configure this template first\n')
        self.git('remote', 'add', 'origin', str(template))
        result = self.run_cli('doctor', code=2)
        self.assertIn('warn: remote origin points at the template', result.stdout)
        self.git('remote', 'rename', 'origin', 'template')
        result = self.run_cli('doctor', code=0)
        self.assertIn('ok: core.hooksPath=githooks', result.stdout)
        self.assertIn('note: Claude hooks load only', result.stdout)

    def test_unset_identity_warns_on_mutations(self):
        self.configure()
        result = self.run_cli('add', '--title', 'X', '--criterion', 'Works', '--prompt', 'prompt.md',
                              '--path', 'example.py', agent=None)
        self.assertIn('WORKFLOW_AGENT is unset', result.stderr)
        self.assertNotIn('WORKFLOW_AGENT is unset', self.run_cli('status', agent=None).stderr)

    def test_new_project_clones_with_fetch_only_template_remote(self):
        self.git_init()
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'template')
        dest = self.root.parent / 'fresh'
        self.run_cli('new', str(dest))
        run = lambda *a: subprocess.run(['git', *a], cwd=dest, capture_output=True, text=True).stdout.strip()
        self.assertEqual(run('remote'), 'template')
        self.assertTrue(run('config', 'remote.template.pushurl').startswith('no-push://'))
        self.assertEqual(run('config', 'user.email'), 'test@example.invalid')
        self.assertEqual(run('config', 'core.hooksPath'), 'githooks')
        self.assertTrue((dest / 'scripts/workflow.py').is_file())
        self.run_cli('new', str(dest), success=False)
        self.configure()
        self.assertIn('configured project', self.run_cli('new', str(self.root.parent / 'other'), success=False).stderr)


    def test_handoffs_reuse_previous_fields_and_attach_memory(self):
        self.configure(); self.add(); self.run_cli('start', 'T001')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', success=False)  # The first handoff needs --file.
        self.handoff('reviewer'); self.review()
        self.run_cli('handoff', 'T001', '--to', 'tester', '--set', 'nope=x', success=False)
        self.run_cli('handoff', 'T001', '--to', 'tester', '--set', 'next_action=Run checks')
        tester = json.loads((self.root / self.task()['handoffs'][-1]).read_text())
        self.assertEqual((tester['objective'], tester['next_action']), ('Works', 'Run checks'))
        self.run_cli('test', 'T001'); self.run_cli('memory', 'T001', '--file', 'memory.md')
        self.run_cli('handoff', 'T001', '--to', 'coordinator')
        final = json.loads((self.root / self.task()['handoffs'][-1]).read_text())
        self.assertEqual(final['memories'], [self.task()['memories'][-1]])
        self.run_cli('done', 'T001')

    def test_inline_request_text_and_follow_ups(self):
        self.configure()
        base = ['add', '--title', 'X', '--criterion', 'Works', '--path', 'example.py']
        self.run_cli(*base, success=False)
        self.run_cli(*base, '--prompt', 'prompt.md', '--prompt-text', 'Both', success=False)
        self.run_cli(*base, '--prompt-text', 'Build the example.')
        self.assertEqual((self.root / self.task()['prompts'][0]).read_text(), 'Build the example.\n')
        self.run_cli(*base, '--prompt-text', 'Later', '--follows', 'T009', success=False)
        self.run_cli(*base[:-1], 'other.py', '--prompt-text', 'Harden it.', '--follows', 'T001')
        self.assertEqual(self.task(1)['follows'], 'T001')
        self.assertIn('Follows: T001', (self.root / 'tasks.md').read_text())
        self.run_cli('start', 'T001'); self.handoff('reviewer'); self.review(); self.handoff('tester')
        self.run_cli('test', 'T001'); self.run_cli('memory', 'T001', '--file', 'memory.md')
        self.handoff('coordinator')
        self.assertIn('open follow-ups: T002', self.run_cli('done', 'T001').stdout)
        journal = (self.root / 'logging/journal.jsonl').read_text()
        self.assertIn('Follow-up to T001', journal)

    def test_independent_review_names_reviewer_and_cites_current_ref(self):
        self.configure(); self.add(); self.run_cli('start', 'T001'); self.handoff('reviewer')
        result = self.review(reviewer=None, success=False)
        self.assertIn('pass --reviewer NAME', result.stderr)
        self.put('review.md', 'Looks fine.\n')
        result = self.run_cli('review', 'T001', '--verdict', 'pass', '--file', 'review.md', '--reviewer', 'reviewer',
                              success=False)
        self.assertIn('must contain the current review_ref', result.stderr)
        stale = json.loads(self.run_cli('context', 'T001', '--role', 'reviewer').stdout)['review_ref']
        self.review('fail')
        self.handoff('reviewer')
        self.put('review.md', f'review_ref: {stale}\n')
        self.run_cli('review', 'T001', '--verdict', 'pass', '--file', 'review.md', '--reviewer', 'reviewer', success=False)
        self.review(reviewer='claude-reviewer')
        record = json.loads((self.root / self.task()['reviews'][-1]).read_text())
        self.assertEqual((record['agent'], record['reviewer']), ('claude', 'claude-reviewer'))
        self.assertEqual(self.task()['reviewed_by'], 'claude-reviewer')
        self.assertIn('Reviewed by: claude-reviewer', (self.root / 'tasks.md').read_text())


    def test_configure_writes_project_readme_but_keeps_an_adopted_one(self):
        self.put('README.md', '# template_llm_mini\n\nTemplate readme.\n')
        self.configure()
        readme = (self.root / 'README.md').read_text()
        self.assertTrue(readme.startswith('# Sample\n'))
        self.assertIn('- Works', readme)
        self.assertIn('docs/workflow.md', readme)
        self.assertIn("-c assert 2 + 2 == 4", readme)

    def test_adopted_readme_is_not_overwritten(self):
        self.put('README.md', '# Existing project\n')
        self.configure()
        self.assertEqual((self.root / 'README.md').read_text(), '# Existing project\n')

    def test_self_test_covers_harness_test_folders(self):
        for folder in ('scripts/tests', 'addons/data_analysis/tests'):
            self.assertIn(f"'{folder}'", (self.root / 'scripts/workflow.py').read_text())
            if folder == 'scripts/tests' or DATA_ADDON:
                self.assertTrue(list((self.root / folder).glob('test_*.py')), folder)
        self.assertEqual([p.name for p in (self.root / 'tests').iterdir()], ['.gitkeep'])


if __name__ == '__main__':
    unittest.main()
