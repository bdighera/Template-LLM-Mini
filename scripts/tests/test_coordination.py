"""Multi-agent coordination, role definitions, and hook/guard enforcement."""
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import unittest

from harness import HarnessTestCase, SOURCE, needs_data_addon

sys.path.insert(0, str(SOURCE / 'scripts'))
import coordination as coord  # noqa: E402

FAKE_KEY = 'AKIA' + 'ABCDEFGHIJKLMNOP'


class ScopeMatchingTests(unittest.TestCase):
    def test_globs_and_overlap_are_conservative(self):
        self.assertTrue(coord.matches('src', 'src/a/b.py'))
        self.assertTrue(coord.matches('src/**', 'src/a/b.py'))
        self.assertTrue(coord.matches('src/**/*.py', 'src/b.py'))
        self.assertFalse(coord.matches('src/*.py', 'src/a/b.py'))
        self.assertTrue(coord.overlaps(['src/**'], ['src/api/x.py']))
        self.assertFalse(coord.overlaps(['src/a/**'], ['src/b/**']))
        self.assertFalse(coord.overlaps(['example.py'], ['tests/**']))
        with self.assertRaises(ValueError):
            coord.normalize_pattern('../outside')


class CoordinationTests(HarnessTestCase):
    def add_task(self, title, *paths, agent='claude'):
        args = ['add', '--title', title, '--criterion', 'Works', '--prompt', 'prompt.md']
        for path in paths:
            args += ['--path', path]
        return self.run_cli(*args, agent=agent).stdout.strip()

    def expire(self, task_id):
        state = self.state()
        for task in state['tasks']:
            if task['id'] == task_id:
                task['claim']['expires'] = '2000-01-01T00:00:00+00:00'
        (self.root / '.workflow/state.json').write_text(json.dumps(state))

    def test_lock_serializes_concurrent_writers(self):
        self.configure()
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(lambda n: self.add_task(f'Task {n}', f'pkg{n}/**', agent='codex' if n % 2 else 'claude'), range(6)))
        self.assertEqual(sorted(ids), [f'T00{n}' for n in range(1, 7)])
        self.assertEqual(len(self.state()['tasks']), 6)
        self.run_cli('check')

    def test_claims_block_overlapping_scopes_across_agents_only(self):
        self.configure()
        self.add_task('API', 'src/**'); self.add_task('Endpoint', 'src/api/**'); self.add_task('Docs', 'docs/**')
        self.run_cli('start', 'T001', agent='claude')
        result = self.run_cli('claim', 'T002', agent='codex', success=False)
        self.assertIn('overlap T001', result.stderr)
        self.run_cli('claim', 'T002', agent='claude')  # Same agent may hold overlapping work.
        self.run_cli('claim', 'T003', agent='codex')
        board = (self.root / 'tasks.md').read_text()
        self.assertIn('| T001 | building | claude |', board)
        self.assertIn('| T003 | planned | codex |', board)

    def test_other_agents_cannot_mutate_a_claimed_task_until_expiry(self):
        self.configure(); self.add(); self.run_cli('start', 'T001', agent='claude')
        self.run_cli('handoff', 'T001', '--to', 'reviewer', '--file', 'handoff.json', agent='codex', success=False)
        self.run_cli('release', 'T001', agent='codex', success=False)
        self.expire('T001')
        self.run_cli('claim', 'T001', agent='codex')
        self.run_cli('heartbeat', 'T001', agent='claude', success=False)
        self.handoff('reviewer', agent='codex')
        self.assertEqual(self.task()['built_by'], 'codex')

    def test_release_heartbeat_and_done_clear_claims(self):
        self.configure(); self.add(); self.run_cli('start', 'T001')
        self.run_cli('heartbeat', 'T001', '--ttl', '5')
        self.run_cli('release', 'T001')
        self.assertIsNone(self.task()['claim'])
        self.run_cli('claim', 'T001')
        self.handoff('reviewer')
        self.review()
        self.handoff('tester'); self.finish()
        self.assertIsNone(self.task()['claim'])

    def test_max_concurrency_counts_agents(self):
        self.config['delegation']['max_concurrency'] = 1
        self.put('config.json', self.config); self.configure()
        self.add_task('A', 'a/**'); self.add_task('B', 'b/**'); self.add_task('C', 'c/**')
        self.run_cli('start', 'T001', agent='claude')
        self.run_cli('start', 'T002', agent='claude')
        self.assertIn('max_concurrency', self.run_cli('claim', 'T003', agent='codex', success=False).stderr)

    def test_scope_changes_recheck_overlap_and_are_logged(self):
        self.configure(); self.add_task('A', 'a/**'); self.add_task('B', 'b/**')
        self.run_cli('start', 'T001', agent='claude'); self.run_cli('start', 'T002', agent='codex')
        self.run_cli('scope', 'T002', '--path', 'a/x.py', agent='codex', success=False)
        self.assertEqual(self.task(1)['paths'], ['b/**'])
        self.run_cli('scope', 'T002', '--path', 'b/**', '--path', 'shared/b.py', agent='codex')
        self.assertIn('Scope paths changed', (self.root / self.task(1)['prompts'][-1]).read_text())

    def test_cross_agent_review_requires_a_different_agent(self):
        self.config['delegation']['review_mode'] = 'cross-agent'
        self.put('config.json', self.config); self.configure(); self.add()
        self.run_cli('start', 'T001', agent='claude'); self.handoff('reviewer', agent='claude')
        self.review(agent='claude', reviewer=None, success=False)
        self.review(agent='codex', reviewer=None)
        review = json.loads((self.root / self.task()['reviews'][-1]).read_text())
        self.assertEqual(review['agent'], 'codex')

    def test_journal_records_agent_identity(self):
        self.configure(); self.add(agent='codex')
        entries = json.loads(self.run_cli('journal', '--all').stdout)['entries']
        self.assertEqual([e['agent'] for e in entries], ['claude', 'codex'])

    def test_data_commands_need_the_addon(self):
        self.configure()
        self.run_cli('add', '--title', 'X', '--criterion', 'Y', '--prompt', 'prompt.md', '--path', 'x.py',
                     '--data', 'expected', success=False)
        self.add(); self.run_cli('start', 'T001')
        self.run_cli('data-check', 'T001', '--phase', 'pre', success=False)
        self.assertNotIn('analysis_stale', json.loads(self.run_cli('context', 'T001').stdout))


class RoleTests(HarnessTestCase):
    def test_roles_need_definition_files_and_custom_roles_are_supported(self):
        self.config['roles'].append('documenter')
        self.put('config.json', self.config)
        self.run_cli('validate-config', '--config', 'config.json', success=False)
        self.put('agents/documenter.md', '---\nname: documenter\ndescription: Writes docs.\nruntime: codex\n'
                 'model_hint: fast\nwrites: [docs/**]\ntools: [read, edit]\nindependent_of: none\nrequires: none\n---\nWrite docs.\n')
        self.configure()
        packet = json.loads(self.run_cli('add', '--title', 'D', '--criterion', 'C', '--prompt', 'prompt.md', '--path', 'docs/**').stdout and
                            self.run_cli('context', 'T001', '--role', 'documenter').stdout)
        self.assertEqual(packet['role_policy']['writes'], ['docs/**'])
        self.assertEqual(packet['role_instruction_ref'], 'agents/documenter.md')
        self.run_cli('context', 'T001', '--role', 'analyzer', success=False)

    def test_sync_agents_generates_claude_subagents_from_definitions(self):
        self.configure()
        reviewer = (self.root / '.claude/agents/reviewer.md').read_text()
        self.assertIn('tools: Read, Grep, Glob', reviewer)
        self.assertIn('model: opus', reviewer)
        self.assertIn('Generated from agents/reviewer.md', reviewer)
        self.assertNotIn('Edit', reviewer.split('---')[1])

    @needs_data_addon
    def test_addon_roles_live_in_agents_and_load_only_when_enabled(self):
        self.config['roles'].append('analyzer')
        self.put('config.json', self.config)
        self.assertIn('enable the add-on', self.run_cli('validate-config', '--config', 'config.json', success=False).stderr)
        self.config['roles'].remove('analyzer'); self.put('config.json', self.config); self.configure()
        self.assertFalse((self.root / '.claude/agents/fetcher.md').exists())

    def test_invalid_frontmatter_is_rejected(self):
        self.put('agents/reviewer.md', '---\nname: reviewer\n---\nNo policy.\n')
        self.run_cli('validate-config', '--config', 'config.json', success=False)


class GuardTests(HarnessTestCase):
    def edit(self, path, agent='claude', role=None, content=None, code=0):
        args = ['guard', 'edit', '--path', path]
        if role:
            args += ['--role', role]
        if content is not None:
            self.put('content.txt', content)
            args += ['--content-file', 'content.txt']
        return json.loads(self.run_cli(*args, agent=agent, code=code).stdout)

    def bash(self, command, code=0):
        return json.loads(self.run_cli('guard', 'bash', '--command', command, code=code).stdout)

    def test_protected_workflow_files_and_raw_data(self):
        for path in ('tasks.md', '.workflow/state.json', 'logging/journal.jsonl', 'memories/index.md', '.claude/agents/builder.md'):
            self.assertEqual(self.edit(path, code=2)['decision'], 'deny', path)
        self.assertEqual(self.edit('data/raw/.gitkeep', code=2)['decision'], 'deny')
        self.assertEqual(self.edit('data/raw/new.csv', code=2)['decision'], 'ask')
        self.assertEqual(self.edit('data/processed/out.csv')['decision'], 'allow')
        self.assertEqual(self.edit('/tmp/outside.txt')['decision'], 'allow')

    def test_source_edits_need_the_agents_own_claim_once_configured(self):
        self.assertEqual(self.edit('scripts/new.py')['decision'], 'allow')  # Unconfigured template.
        self.configure(); self.add(paths=('scripts/feature/**',))
        self.assertIn('no live claim', self.edit('scripts/feature/a.py', code=2)['reason'])
        self.assertEqual(self.edit('README.md')['decision'], 'allow')  # Not a selected source path.
        self.run_cli('start', 'T001', agent='claude')
        self.assertEqual(self.edit('scripts/feature/a.py')['decision'], 'allow')
        self.assertIn('claimed by claude', self.edit('scripts/feature/a.py', agent='codex', code=2)['reason'])
        self.assertIn('no live claim', self.edit('scripts/other.py', code=2)['reason'])
        self.assertIn('may not write', self.edit('scripts/feature/a.py', role='reviewer', code=2)['reason'])

    def test_secrets_and_shell_commands(self):
        self.assertEqual(self.edit('notes.md', content=f'key = {FAKE_KEY}', code=2)['decision'], 'ask')
        self.assertEqual(self.bash('echo x > tasks.md', code=2)['decision'], 'deny')
        self.assertEqual(self.bash('sed -i s/a/b/ logging/journal.jsonl', code=2)['decision'], 'deny')
        self.assertEqual(self.bash('rm data/raw/input.csv', code=2)['decision'], 'deny')
        self.assertEqual(self.bash('curl -O https://example.com/x.csv', code=2)['decision'], 'ask')
        self.assertEqual(self.bash('git commit --no-verify -m x', code=2)['decision'], 'deny')
        self.assertEqual(self.bash('git -c core.hookspath=/dev/null commit -m x', code=2)['decision'], 'deny')
        self.assertEqual(self.bash('cat tasks.md .workflow/state.json')['decision'], 'allow')
        self.assertEqual(self.bash('python3 scripts/workflow.py status 2>&1')['decision'], 'allow')

    def test_claude_hook_protocol(self):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Write',
                   'tool_input': {'file_path': str(self.root / 'tasks.md'), 'content': 'x'}}
        out = json.loads(self.run_cli('guard', 'hook', stdin=json.dumps(payload)).stdout)
        self.assertEqual(out['hookSpecificOutput']['permissionDecision'], 'deny')
        payload['tool_input'] = {'file_path': str(self.root / 'notes.md'), 'content': 'fine'}
        self.assertEqual(self.run_cli('guard', 'hook', stdin=json.dumps(payload)).stdout, '')
        self.configure(); self.add(); self.run_cli('start', 'T001')
        self.run_cli('checkpoint', 'T001', '--file', 'handoff.json')
        start = {'hook_event_name': 'SessionStart', 'source': 'startup'}
        text = self.run_cli('guard', 'hook', stdin=json.dumps(start)).stdout
        self.assertIn('T001 building [claude until', text)
        self.assertNotIn('Resume packet', text)
        start['source'] = 'compact'
        self.assertIn('Resume packet', self.run_cli('guard', 'hook', stdin=json.dumps(start)).stdout)

    def test_settings_wire_hooks_to_guard(self):
        settings = json.loads((self.root / '.claude/settings.json').read_text())
        self.assertEqual(settings['env']['WORKFLOW_AGENT'], 'claude')
        commands = [h['command'] for event in settings['hooks'].values() for entry in event for h in entry['hooks']]
        self.assertTrue(all('workflow.py' in c and 'guard' in c for c in commands))


class CommitHookTests(HarnessTestCase):
    def commit(self, success=True):
        result = self.git('commit', '-q', '-m', 'change', check=False)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_pre_commit_blocks_data_secrets_and_journal_rewrites(self):
        self.git_init(); self.configure()
        self.assertEqual(self.git('config', 'core.hooksPath').stdout.strip(), 'githooks')
        self.git('add', '-A'); self.commit()
        self.put('data/raw/input.csv', 'id\n1\n'); self.git('add', '-f', 'data/raw/input.csv')
        self.assertIn('data contents stay out of Git', self.commit(success=False).stderr)
        self.git('rm', '-q', '--cached', 'data/raw/input.csv')
        self.put('notes.md', f'token {FAKE_KEY}\n'); self.git('add', 'notes.md')
        self.assertIn('possible AWS access key', self.commit(success=False).stderr)
        self.put('notes.md', 'clean\n'); self.git('add', 'notes.md'); self.commit()
        journal = self.root / 'logging/journal.jsonl'
        journal.write_text(journal.read_text().replace('Initial configuration', 'Rewritten history'))
        self.git('add', 'logging/journal.jsonl')
        self.assertIn('must only grow', self.commit(success=False).stderr)
        self.git('reset', '-q'); self.git('checkout', '--', '.')
        self.add(); self.git('add', '-A'); self.commit()  # Appends are fine.


class PruneTests(HarnessTestCase):
    ALL = ['data_analysis', 'template_history', 'initialization_primer', 'configuration_interview', 'claude_integration']

    @needs_data_addon
    def test_configure_removes_every_pruned_component_and_workflow_still_runs(self):
        self.put('docs/template-history/README.md', 'history\n')
        self.config['prune'] = self.ALL
        self.put('config.json', self.config); self.prepare_testing()
        for ref in ('addons/data_analysis', 'agents/analyzer.md', 'agents/fetcher.md',
                    'docs/template-history', 'prompts/template_prompt_initialization.md', 'CONFIGURE.md',
                    'CLAUDE.md', '.claude/settings.json', '.claude/agents'):
            self.assertFalse((self.root / ref).exists(), ref)
        self.assertTrue((self.root / 'agents/builder.md').exists())
        self.assertTrue((self.root / 'docs/workflow.md').exists())  # Core docs stay; only history goes.
        self.finish(); self.run_cli('check')
        milestone = json.loads(self.run_cli('journal', '--id', 'J000001', '--full').stdout)['entries'][0]
        self.assertIn('addons/data_analysis', milestone['outcome'])
        self.run_cli('sync-agents', success=False)

    @needs_data_addon
    def test_prune_names_and_enabled_addon_are_validated(self):
        for prune, enabled in ((['unknown'], False), (['data_analysis'], True), (['template_history'] * 2, False)):
            with self.subTest(prune=prune):
                config = json.loads(json.dumps(self.config))
                config['prune'] = prune
                if enabled:
                    config['addons']['data_analysis'] = True
                    config['analysis'] = json.loads((self.root / 'addons/data_analysis/config.json').read_text())
                self.put('bad.json', config)
                self.run_cli('validate-config', '--config', 'bad.json', success=False)

    @needs_data_addon
    def test_update_config_previews_removals_before_deleting(self):
        self.configure()
        self.config['prune'] = ['data_analysis', 'configuration_interview']
        self.put('candidate.json', self.config)
        preview = json.loads(self.run_cli('update-config', '--config', 'candidate.json').stdout)
        self.assertIn('addons/data_analysis', preview['removals'])
        self.assertIn('agents/fetcher.md', preview['removals'])
        self.assertTrue((self.root / 'CONFIGURE.md').exists())
        self.run_cli('update-config', '--config', 'candidate.json', '--approved', '--preview-hash', preview['preview_hash'])
        self.assertFalse((self.root / 'CONFIGURE.md').exists())
        self.assertFalse((self.root / 'addons/data_analysis').exists())


if __name__ == '__main__':
    unittest.main()
