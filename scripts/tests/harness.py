"""Shared fixture: an isolated copy of the harness source, configured per test."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2]
# Configured projects may prune the add-on; its tests then skip instead of failing.
DATA_ADDON = (SOURCE / 'addons/data_analysis').is_dir()
needs_data_addon = unittest.skipUnless(DATA_ADDON, 'data-analysis add-on pruned')


def harness_files(source):
    """Harness source only, never adopted state, datasets, or environments."""
    names = ['.gitignore', '.gitattributes', 'AGENTS.md', 'CLAUDE.md', 'README.md', 'CONFIGURE.md', 'docs/workflow.md',
             'prompts/template_prompt_initialization.md', '.claude/settings.json', 'tests/.gitkeep']
    for folder in ('scripts', 'templates', 'agents', 'addons', 'githooks'):
        names += [p.relative_to(source).as_posix() for p in (source / folder).rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts]
    names += [f'data/{folder}/.gitkeep' for folder in ('raw', 'processed', 'generated')]
    return [name for name in names if (source / name).is_file()]


class HarnessTestCase(unittest.TestCase):
    data_addon = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        for name in harness_files(SOURCE):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / name, destination)
        c = json.loads((self.root / 'templates/config.json').read_text())
        c['project'] = dict(name='Sample', purpose='Example', users='Developer',
                            scope='One feature', exclusions='Deployment', acceptance_criteria=['Works'])
        c['technology'] = 'Python'
        c['data'].update(usage='Synthetic examples only', sources='none',
                         sensitivity='Public synthetic data', expected_size='Under 1 MB',
                         retention='Keep for project lifetime', generated_outputs='Example records')
        c['delegation']['capabilities'] = 'Independent review session'
        c['validation']['commands'] = [[sys.executable, '-c', 'assert 2 + 2 == 4']]
        c['approvals'] = 'Configuration and external publication'
        c['delivery'] = 'Local working feature'
        if self.data_addon:
            c['addons']['data_analysis'] = True
            c['analysis'] = json.loads((self.root / 'addons/data_analysis/config.json').read_text())
        self.config = c
        self.put('config.json', c)
        self.put('example.py', 'assert 2 + 2 == 4\n')
        self.put('prompt.md', 'Add an example feature.\n')
        self.put('review.md', 'Reviewed implementation against Works; no findings.\n')
        self.put('memory.md', 'Behavior: example. Decision: small. Validation: arithmetic. Limitations: synthetic.\n')
        self.put('handoff.json', dict(objective='Works', files=['example.py'], memories=[],
                 decisions='Small scope', changes='Example', validation='See recorded checks',
                 unresolved='none', next_action='Evaluate acceptance criteria'))

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) if not isinstance(data, str) else data)

    def run_cli(self, *args, success=True, agent='claude', stdin=None, code=None):
        env = dict(os.environ, WORKFLOW_LOCK_TIMEOUT='10')
        env.pop('WORKFLOW_ROLE', None)
        env.pop('CLAUDE_PROJECT_DIR', None)
        if agent is None:  # Simulate an unset identity.
            env.pop('WORKFLOW_AGENT', None)
        else:
            env['WORKFLOW_AGENT'] = agent
        result = subprocess.run([sys.executable, str(self.root / 'scripts/workflow.py'), *args],
                                cwd=self.root, text=True, encoding='utf-8', capture_output=True, env=env, input=stdin)
        if code is not None:
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def configure(self):
        self.run_cli('configure', '--config', 'config.json', '--approved')

    def add(self, depends=None, data='none', phases=None, paths=('example.py',), agent='claude'):
        args = ['add', '--significance', 'routine', '--title', 'Example', '--criterion', 'Works', '--prompt', 'prompt.md']
        for path in paths:
            args += ['--path', path]
        if self.data_addon:
            args += ['--data', data]
            if data == 'none':
                args += ['--data-reason', 'Arithmetic fixture does not require datasets']
            for phase in phases or []:
                args += ['--analysis-phase', phase]
        if depends:
            args += ['--depends', depends]
        return self.run_cli(*args, agent=agent).stdout.strip()

    def review(self, verdict='pass', task='T001', agent='claude', reviewer='reviewer', success=True):
        """Record a review citing the current review_ref, as an independent reviewer would."""
        packet = json.loads(self.run_cli('context', task, '--role', 'reviewer', agent=agent).stdout)
        self.put('review.md', f'review_ref: {packet["review_ref"]}\nReviewed implementation against Works; no findings.\n')
        args = ['review', task, '--verdict', verdict, '--file', 'review.md'] + (['--reviewer', reviewer] if reviewer else [])
        return self.run_cli(*args, agent=agent, success=success)

    def handoff(self, target, task='T001', agent='claude'):
        self.run_cli('handoff', task, '--to', target, '--file', 'handoff.json', agent=agent)

    def state(self):
        return json.loads((self.root / '.workflow/state.json').read_text())

    def task(self, index=0):
        return self.state()['tasks'][index]

    def prepare_testing(self):
        self.configure(); self.add(); self.run_cli('start', 'T001')
        self.handoff('reviewer')
        self.review()
        self.handoff('tester')

    def finish(self):
        self.run_cli('test', 'T001')
        self.run_cli('memory', 'T001', '--file', 'memory.md')
        self.handoff('coordinator'); self.run_cli('done', 'T001')

    def git_init(self):
        if not shutil.which('git'):
            self.skipTest('Git is required')
        for args in (['init', '-q'], ['config', 'user.email', 'test@example.invalid'], ['config', 'user.name', 'Test']):
            subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)

    def git(self, *args, check=True):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True, encoding='utf-8', check=check)
