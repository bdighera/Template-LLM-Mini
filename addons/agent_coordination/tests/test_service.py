"""Portable real-SQLite, real-worktree and loopback HTTP/MCP behavior tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SOURCE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE / 'scripts/tests'))
sys.path.insert(0, str(SOURCE / 'addons/agent_coordination'))
from harness import HarnessTestCase

AVAILABLE = all(importlib.util.find_spec(name) for name in ('fastapi', 'uvicorn', 'pydantic', 'mcp'))
if AVAILABLE:
    from contracts import Assignment, OPERATIONS
    from service import Service, Fault
    from api import create_app
    from client import Client
    from pydantic import ValidationError
    import uvicorn


@unittest.skipUnless(AVAILABLE, 'Optional API/MCP dependencies are not installed')
class CoordinationTests(HarnessTestCase):
    def setUp(self):
        super().setUp()
        original = self.root
        self.root = original.with_name('authoritative project')
        original.rename(self.root)
        self.configure(); self.add()
        self.git_init(); self.git('branch', '-M', 'coordinator')
        self.git('add', '.'); self.git('commit', '-m', 'Configured synthetic fixture')
        self.worker = self.root.parent / 'worker with spaces'
        self.git('worktree', 'add', '-b', 'worker-one', str(self.worker))
        self.tokens = {'orchestrator': 'o' * 40, 'worker': 'w' * 40, 'reviewer': 'r' * 40}
        self.settings = {'version': '1', 'principals': [
            {'agent': agent, 'role': agent, 'token_env': 'COORD_' + agent.upper(), 'tasks': ['T001']}
            for agent in self.tokens]}
        self.env = patch.dict(os.environ, {'COORD_' + a.upper(): t for a, t in self.tokens.items()})
        self.env.start(); self.addCleanup(self.env.stop)
        self.service = Service(self.root, self.settings)
        self.count = 0
        for agent in self.tokens:
            self.call('register', agent=agent, display_name=agent)
        self.assignment = Assignment(task_id='T001', recipient='worker', paths=['example.py'],
                                     acceptance_criteria=['Works'], worktree=str(self.worker), branch='worker-one')

    def call(self, operation, agent='orchestrator', request_id=None, **payload):
        self.count += 1
        return self.service.exchange(self.tokens[agent], {'version': '1', 'operation': operation,
                'request_id': request_id or f'request-{self.count}', 'correlation_id': 'discussion-1', **payload})

    def assign(self):
        grant = self.service.approval(self.assignment)
        return self.call('assign', approval_id=grant['approval_id'], assignment=self.assignment.model_dump())['result']['assignment_id']

    def start_http(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(self.service), log_level='error', access_log=False))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (setattr(server, 'should_exit', True), thread.join(10), listener.close()))
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(server.started)
        return f'http://127.0.0.1:{port}'

    def test_strict_schema_and_identity(self):
        request = dict(operation='register', version='1', request_id='x', correlation_id='y', display_name='Example')
        for extra in ({'role': 'orchestrator'}, {'approved': True}, {'version': '2'}, {'display_name': 42}):
            with self.assertRaises(ValidationError):
                OPERATIONS.validate_python(dict(request, **extra))
        with self.assertRaises(Fault):
            self.service.exchange('wrong', request)
        self.assertEqual(self.call('register', agent='worker', display_name='Worker')['result']['role'], 'worker')

    def test_messages_acknowledgement_and_scopes(self):
        mid = self.call('send', recipient='worker', kind='question', task_id='T001', body='Please review')['result']['message_id']
        inbox = self.call('inbox', agent='worker')['result']['messages']
        self.assertEqual(inbox[0]['message_id'], mid)
        self.assertEqual(inbox[0]['correlation_id'], 'discussion-1')
        self.assertEqual(len(self.call('inbox', agent='worker')['result']['messages']), 1)  # redelivery
        with self.assertRaises(Fault):
            self.call('acknowledge', agent='reviewer', message_id=mid)
        self.call('acknowledge', agent='worker', message_id=mid)
        self.assertFalse(self.call('inbox', agent='worker')['result']['messages'])
        with self.assertRaises(Fault):
            self.call('send', recipient='worker', kind='discussion', task_id='T999', body='Out of scope')
        with self.assertRaises(Fault):
            self.call('send', recipient='worker', kind='discussion', body='Escape', artifact_refs=['../outside'])

    def test_duplicate_concurrent_requests_and_restart(self):
        payload = {'operation': 'send', 'version': '1', 'request_id': 'unique-command', 'correlation_id': 'same',
                   'recipient': 'worker', 'kind': 'discussion', 'body': 'One logical message'}
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.service.exchange(self.tokens['orchestrator'], payload), range(12)))
        self.assertTrue(all(r == results[0] for r in results))
        self.service = Service(self.root, self.settings)
        self.assertEqual(self.service.exchange(self.tokens['orchestrator'], payload), results[0])
        self.assertEqual(len(self.call('inbox', agent='worker')['result']['messages']), 1)
        with self.assertRaises(Fault):
            self.service.exchange(self.tokens['orchestrator'], dict(payload, body='Changed command'))

    def test_current_permissions_on_replay_and_queued_accept(self):
        identifier = self.assign()
        settings = copy.deepcopy(self.settings)
        settings['principals'][1]['role'] = 'reviewer'
        self.service = Service(self.root, settings)
        with self.assertRaises(Fault):
            self.call('accept', agent='worker', assignment_id=identifier)
        self.assertEqual(self.task()['status'], 'planned')
        self.call('send', request_id='replayed', recipient='worker', task_id='T001', kind='finding', body='Scoped finding')
        settings['principals'][0]['tasks'] = []
        self.service = Service(self.root, settings)
        with self.assertRaises(Fault):
            self.call('send', request_id='replayed', recipient='worker', task_id='T001', kind='finding', body='Scoped finding')

    def test_revoked_inbox_rows_do_not_starve_authorized_messages(self):
        for i in range(4):
            self.call('send', recipient='worker', task_id='T001', kind='finding', body=f'Scoped {i}')
        self.call('send', recipient='worker', kind='discussion', body='General discussion')
        settings = copy.deepcopy(self.settings); settings['principals'][1]['tasks'] = []
        self.service = Service(self.root, settings)
        result = self.call('inbox', agent='worker', limit=1)['result']
        self.assertEqual(result['messages'][0]['content']['body'], 'General discussion')

    def test_assignment_requires_exact_local_approval(self):
        with self.assertRaises(Fault):
            self.call('assign', approval_id='missing', assignment=self.assignment.model_dump())
        grant = self.service.approval(self.assignment)
        changed = self.assignment.model_copy(update={'paths': ['other.py']})
        with self.assertRaises(Fault):
            self.call('assign', approval_id=grant['approval_id'], assignment=changed.model_dump())
        with self.assertRaises(Fault):
            self.call('assign', agent='worker', approval_id=grant['approval_id'], assignment=self.assignment.model_dump())
        identifier = self.call('assign', approval_id=grant['approval_id'], assignment=self.assignment.model_dump())['result']['assignment_id']
        with self.assertRaises(Fault):
            self.call('assign', approval_id=grant['approval_id'], assignment=self.assignment.model_dump())
        self.assertTrue(identifier)

    def test_stale_source_and_worker_branch_rejected(self):
        grant = self.service.approval(self.assignment)
        (self.worker / 'example.py').write_text('print("changed worker source")\n')
        with self.assertRaises(Fault):
            self.call('assign', approval_id=grant['approval_id'], assignment=self.assignment.model_dump())
        self.git('worktree', 'remove', '--force', str(self.worker))
        with self.assertRaises(Fault):
            self.service.approval(self.assignment)

    def test_accept_starts_once_and_result_is_not_workflow_completion(self):
        identifier = self.assign()
        self.call('accept', agent='worker', request_id='accept-1', assignment_id=identifier)
        self.assertEqual(self.task()['status'], 'building')
        self.assertEqual(self.task()['claim']['agent'], 'worker')
        count = len(self.task()['prompts'])
        self.call('accept', agent='worker', request_id='accept-1', assignment_id=identifier)
        self.assertEqual(len(self.task()['prompts']), count)
        self.call('result', agent='worker', assignment_id=identifier, outcome='completed', summary='Proposed result')
        self.assertEqual(self.task()['status'], 'building')
        self.assertEqual(self.call('inspect', assignment_id=identifier)['result']['status'], 'reported')

    def test_workflow_commit_before_service_receipt_recovers(self):
        identifier = self.assign()
        original = self.service.bridge.start
        def interrupted(*args):
            original(*args)
            raise RuntimeError('Synthetic interruption after durable workflow write')
        with patch.object(self.service.bridge, 'start', side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.call('accept', agent='worker', request_id='restart-accept', assignment_id=identifier)
        self.assertEqual(self.task()['status'], 'building')
        self.service = Service(self.root, self.settings)
        self.call('accept', agent='worker', request_id='restart-accept', assignment_id=identifier)
        self.assertEqual(self.call('inspect', assignment_id=identifier)['result']['status'], 'accepted')

    def test_workflow_north_star_and_significance_gates_preserved(self):
        identifier = self.assign()
        self.put('NORTH_STAR.md', 'Synthetic unapproved vision')
        with self.assertRaises(Fault):
            self.call('accept', agent='worker', assignment_id=identifier)
        self.assertEqual(self.task()['status'], 'planned')
        (self.root / 'NORTH_STAR.md').unlink()
        state = self.state(); state['tasks'][0]['significant'] = True
        self.put('.workflow/state.json', state)
        identifier = self.assign()
        with self.assertRaises(Fault):
            self.call('accept', agent='worker', assignment_id=identifier)
        self.assertEqual(self.task()['status'], 'planned')

    def test_expired_approval_and_worktree_scope_rejected(self):
        grant = self.service.approval(self.assignment)
        with self.service.connect() as db:
            db.execute("UPDATE approvals SET expires='2000-01-01' WHERE id=?", (grant['approval_id'],))
        with self.assertRaises(Fault):
            self.call('assign', approval_id=grant['approval_id'], assignment=self.assignment.model_dump())
        for changed in ({'worktree': str(self.root)}, {'branch': 'other-branch'}, {'acceptance_criteria': ['Different']}):
            with self.assertRaises(Fault):
                self.service.approval(self.assignment.model_copy(update=changed))

    def test_relative_worktree_and_git_failures_are_workflow_faults(self):
        relative = self.assignment.model_copy(update={'worktree': self.worker.name})  # relpath fails across Windows drives
        with self.assertRaises(Fault) as caught:
            self.service.approval(relative)
        self.assertEqual((caught.exception.status, caught.exception.message), (409, 'Worker worktree must be an absolute path'))
        timeout = subprocess.TimeoutExpired('git', 10)
        with patch('bridge.subprocess.run', side_effect=timeout), self.assertRaises(Fault) as caught:
            self.service.approval(self.assignment)
        self.assertEqual(caught.exception.status, 409)
        grant = self.service.approval(self.assignment)
        with patch('bridge.subprocess.run', side_effect=timeout), self.assertRaises(Fault) as caught:
            self.call('assign', approval_id=grant['approval_id'], assignment=self.assignment.model_dump())
        self.assertEqual(caught.exception.status, 409)

    def test_http_schema_authorization_and_body_limits(self):
        origin = self.start_http(); client = Client(origin, self.tokens['worker'])
        self.assertIn('oneOf', client.request('/v1/schema'))
        self.assertIn('/v1/exchange', client.request('/v1/openapi.json')['paths'])
        for path in ('/v1/schema', '/v1/openapi.json'):
            with self.assertRaises(HTTPError) as result:
                urlopen(origin + path, timeout=5)
            self.assertEqual(result.exception.code, 401)
        invalid = client.exchange({'operation': 'approve', 'version': '1', 'request_id': 'invalid', 'correlation_id': 'x'})
        self.assertEqual(invalid['error']['code'], 'schema')
        request = Request(origin + '/v1/exchange', data=b' ' * 65537, headers={'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as result:
            urlopen(request, timeout=5)
        self.assertEqual(result.exception.code, 413)
        for url in ('http://example.com:80', 'http://127.0.0.1:80/escape', 'https://127.0.0.1:80', 'http://localhost:80'):
            with self.assertRaises(ValueError):
                Client(url, self.tokens['worker'])

    def test_real_mcp_stdio_matches_http_and_denies_approval(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        origin = self.start_http()
        async def check():
            env = dict(os.environ, AGENT_COORD_TOKEN=self.tokens['worker'])
            parameters = StdioServerParameters(command=sys.executable,
                         args=[str(SOURCE / 'addons/agent_coordination/cli.py'), 'mcp', '--url', origin], env=env)
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({tool.name for tool in tools.tools}, {'coordination_schema','coordination_exchange'})
                    request = dict(version='1', operation='workflow_status', request_id='mcp-status', correlation_id='comparison', task_id='T001')
                    result = await session.call_tool('coordination_exchange', {'request': request})
                    text = '\n'.join(item.text for item in result.content if hasattr(item, 'text'))
                    self.assertEqual(json.loads(text), Client(origin, self.tokens['worker']).exchange(request))
                    request['operation'] = 'approve'
                    result = await session.call_tool('coordination_exchange', {'request': request})
                    text = '\n'.join(item.text for item in result.content if hasattr(item, 'text'))
                    self.assertEqual(json.loads(text)['error']['code'], 'schema')
        asyncio.run(check())


    def test_large_inbox_and_strict_json_transport(self):
        origin = self.start_http()
        for i in range(70):
            self.call('send', recipient='worker', kind='discussion', body='漢' * 12000)
        client = Client(origin, self.tokens['worker'])
        response = client.exchange({'version':'1','operation':'inbox','request_id':'large-page',
                                    'correlation_id':'large','limit':100})
        self.assertEqual(len(response['result']['messages']), 70)
        self.assertGreater(len(json.dumps(response, ensure_ascii=False).encode()), 2_000_000)
        for body in (b'{"version":"1","version":"2"}', b'{"value":NaN}'):
            request = Request(origin + '/v1/exchange', data=body,
                              headers={'Authorization':'Bearer ' + self.tokens['worker'], 'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as result:
                urlopen(request, timeout=5)
            self.assertEqual(result.exception.code, 422)
            self.assertNotIn(self.tokens['worker'], result.exception.read().decode())

    def test_operator_approval_needs_no_server_tokens(self):
        with patch.dict(os.environ, {p['token_env']: '' for p in self.settings['principals']}):
            operator = Service(self.root, self.settings, load_credentials=False)
            self.assertTrue(operator.approval(self.assignment)['approval_id'])
            with self.assertRaises(Fault):
                Service(self.root, self.settings)


if __name__ == '__main__':
    unittest.main()
