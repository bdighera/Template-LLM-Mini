"""Deterministic coordination: authenticated identities, transactions, and scoped grants."""
from __future__ import annotations
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import uuid

from contracts import OPERATIONS, Assignment, Settings
from bridge import Bridge


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class Fault(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def require(condition, code, message, status=400):
    if not condition:
        raise Fault(code, message, status)


class Service:
    def __init__(self, root, settings, load_credentials=True):
        self.bridge = Bridge(root)
        self.settings = Settings.model_validate(settings)
        self.principals = {p.agent: p for p in self.settings.principals}
        require(len(self.principals) == len(self.settings.principals), 'settings', 'Duplicate agent identity')
        self.tokens = {}
        for principal in self.principals.values() if load_credentials else []:
            token = os.environ.get(principal.token_env, '')
            require(len(token) >= 32, 'settings', 'Each agent needs its own token of at least 32 characters')
            hashed = hashlib.sha256(token.encode()).digest()
            require(hashed not in self.tokens, 'settings', 'Agent credentials must be distinct')
            self.tokens[hashed] = principal
        directory = self.bridge.root / '.workflow/agent-coordination'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not directory.is_symlink() and directory.resolve().is_relative_to(self.bridge.root), 'settings', 'Unsafe state directory')
        self.database = directory / 'service.sqlite3'
        require(not self.database.is_symlink(), 'settings', 'Unsafe database path')
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS metadata(version INTEGER NOT NULL);
                INSERT INTO metadata SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM metadata);
                CREATE TABLE IF NOT EXISTS agents(agent TEXT PRIMARY KEY, display_name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                  sender TEXT NOT NULL, recipient TEXT NOT NULL, task_id TEXT,
                  correlation_id TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
                  created TEXT NOT NULL, acknowledged TEXT);
                CREATE TABLE IF NOT EXISTS approvals(
                  id TEXT PRIMARY KEY, assignment TEXT NOT NULL, stamp TEXT NOT NULL,
                  expires TEXT NOT NULL, used_by TEXT);
                CREATE TABLE IF NOT EXISTS assignments(
                  id TEXT PRIMARY KEY, sender TEXT NOT NULL, approval_id TEXT UNIQUE NOT NULL,
                  content TEXT NOT NULL, status TEXT NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS operations(
                  actor TEXT NOT NULL, request_id TEXT NOT NULL, hash TEXT NOT NULL,
                  response TEXT NOT NULL, PRIMARY KEY(actor,request_id));
                CREATE TABLE IF NOT EXISTS audit(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                  request_id TEXT NOT NULL, correlation_id TEXT NOT NULL,
                  operation TEXT NOT NULL, created TEXT NOT NULL);
            ''')
            require(db.execute('SELECT version FROM metadata').fetchall() == [(1,)], 'schema', 'Unsupported database schema')
        if os.name != 'nt':
            self.database.chmod(0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        db.execute('PRAGMA busy_timeout=15000')
        try:
            yield db
        finally:
            db.close()

    def authenticate(self, token):
        hashed = hashlib.sha256(token.encode()).digest()
        found = next((p for value, p in self.tokens.items() if hmac.compare_digest(value, hashed)), None)
        require(found is not None, 'unauthorized', 'Valid agent credentials required', 401)
        return found

    def scope(self, principal, task_id):
        require(task_id in principal.tasks, 'forbidden', 'Task is outside agent scope', 403)

    def references(self, refs):
        for ref in refs:
            path = PurePosixPath(ref)
            require(not path.is_absolute() and '..' not in path.parts and ':' not in ref and '\\' not in ref
                    and '\x00' not in ref, 'reference', 'Artifact references must be relative repository paths')
            require(not any(part.startswith('.env') for part in path.parts), 'reference', 'Credential references are not allowed')
            require((self.bridge.root / ref).resolve().is_relative_to(self.bridge.root), 'reference', 'Artifact reference escapes repository')

    def approval(self, assignment, ttl=3600):
        """Local-operator-only capability. Never exposed as HTTP or MCP operation."""
        require(60 <= ttl <= 86400, 'approval', 'Approval lifetime must be 60..86400 seconds')
        recipient = self.principals.get(assignment.recipient)
        require(recipient is not None and recipient.role == 'worker', 'approval', 'Assignments require an enrolled editing worker')
        self.scope(recipient, assignment.task_id)
        try:
            with self.bridge.locked():
                stamp = self.bridge.validate_assignment(assignment)
        except (ValueError, OSError) as exc:
            raise Fault('workflow', str(exc), 409) from exc
        identifier = uuid.uuid4().hex
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        with self.connect() as db:
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,NULL)',
                       (identifier, canonical(assignment.model_dump()), stamp, expires))
            db.execute('INSERT INTO audit(actor,request_id,correlation_id,operation,created) VALUES(?,?,?,?,?)',
                       ('local-operator', identifier, identifier, 'local_approval_asserted', now()))
        return {'approval_id': identifier, 'assignment': assignment.model_dump(), 'expires': expires}

    def message(self, db, sender, recipient, task, correlation, kind, content):
        identifier = uuid.uuid4().hex
        db.execute('INSERT INTO messages(id,sender,recipient,task_id,correlation_id,kind,content,created) VALUES(?,?,?,?,?,?,?,?)',
                   (identifier, sender, recipient, task, correlation, kind, canonical(content), now()))
        return identifier

    def exchange(self, token, raw):
        actor = self.authenticate(token)
        require(len(canonical(raw).encode()) <= 65536, 'body_limit', 'JSON body exceeds 64 KiB', 413)
        operation = OPERATIONS.validate_python(raw)
        payload = operation.model_dump()
        request_hash = digest({'payload': payload, 'principal': actor.model_dump()})
        write = operation.operation not in ('inbox', 'inspect', 'workflow_status')
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                try:
                    if write:
                        receipt = db.execute('SELECT hash,response FROM operations WHERE actor=? AND request_id=?',
                                             (actor.agent, operation.request_id)).fetchone()
                        if receipt:
                            require(receipt[0] == request_hash, 'idempotency_conflict', 'Request ID was used with different content', 409)
                            return json.loads(receipt[1])
                    result = self.dispatch(db, actor, operation)
                    response = dict(version='1', request_id=operation.request_id, correlation_id=operation.correlation_id, result=result)
                    if write:
                        db.execute('INSERT INTO operations VALUES(?,?,?,?)',
                                   (actor.agent, operation.request_id, request_hash, canonical(response)))
                        db.execute('INSERT INTO audit(actor,request_id,correlation_id,operation,created) VALUES(?,?,?,?,?)',
                                   (actor.agent, operation.request_id, operation.correlation_id, operation.operation, now()))
                    db.commit()
                    return response
                except BaseException:
                    db.rollback()
                    raise
        except sqlite3.OperationalError as exc:
            raise Fault('store_busy', 'Coordination store unavailable; retry with the same request ID', 503) from exc
        except (ValueError, OSError) as exc:
            raise Fault('workflow', str(exc), 409) from exc

    def dispatch(self, db, actor, op):
        if op.operation == 'register':
            db.execute('INSERT INTO agents VALUES(?,?) ON CONFLICT(agent) DO UPDATE SET display_name=excluded.display_name',
                       (actor.agent, op.display_name))
            return {'agent': actor.agent, 'role': actor.role, 'tasks': actor.tasks}
        require(db.execute('SELECT 1 FROM agents WHERE agent=?', (actor.agent,)).fetchone(), 'registration', 'Register this enrolled identity first', 403)
        if op.operation == 'send':
            recipient = self.principals.get(op.recipient)
            require(recipient is not None, 'recipient', 'Unknown recipient')
            if op.task_id:
                self.scope(actor, op.task_id); self.scope(recipient, op.task_id)
            self.references(op.artifact_refs)
            mid = self.message(db, actor.agent, op.recipient, op.task_id, op.correlation_id, op.kind,
                               {'body': op.body, 'artifact_refs': op.artifact_refs})
            return {'message_id': mid}
        if op.operation == 'inbox':
            placeholders = ','.join('?' for _ in actor.tasks) or 'NULL'
            rows = db.execute('SELECT seq,id,sender,task_id,correlation_id,kind,content,created FROM messages '
                              'WHERE recipient=? AND seq>? AND acknowledged IS NULL AND '
                              f'(task_id IS NULL OR task_id IN ({placeholders})) ORDER BY seq LIMIT ?',
                              (actor.agent, op.after, *actor.tasks, op.limit)).fetchall()
            messages = [dict(zip(('sequence','message_id','sender','task_id','correlation_id','kind','content','created'), row)) for row in rows]
            for item in messages:
                item['content'] = json.loads(item['content'])
            return {'messages': messages, 'next_cursor': rows[-1][0] if rows else op.after}
        if op.operation == 'acknowledge':
            message = db.execute('SELECT recipient,acknowledged FROM messages WHERE id=?', (op.message_id,)).fetchone()
            require(message is not None and message[0] == actor.agent, 'forbidden', 'Only the recipient may acknowledge', 403)
            timestamp = message[1] or now()
            db.execute('UPDATE messages SET acknowledged=? WHERE id=?', (timestamp, op.message_id))
            return {'message_id': op.message_id, 'acknowledged': timestamp}
        if op.operation == 'workflow_status':
            self.scope(actor, op.task_id)
            with self.bridge.locked():
                return self.bridge.status(op.task_id)
        if op.operation == 'assign':
            require(actor.role == 'orchestrator', 'forbidden', 'Only orchestrators may assign tasks', 403)
            assignment = op.assignment
            self.scope(actor, assignment.task_id)
            recipient = self.principals.get(assignment.recipient)
            require(recipient is not None and recipient.role == 'worker', 'forbidden', 'Enrolled editing worker required', 403)
            self.scope(recipient, assignment.task_id)
            grant = db.execute('SELECT assignment,stamp,expires,used_by FROM approvals WHERE id=?', (op.approval_id,)).fetchone()
            require(grant is not None and grant[0] == canonical(assignment.model_dump()), 'approval', 'Local approval must match the exact assignment', 403)
            require(grant[2] > now() and grant[3] is None, 'approval', 'Approval expired or already consumed', 409)
            with self.bridge.locked():
                require(self.bridge.validate_assignment(assignment) == grant[1], 'stale_approval', 'Task or source changed; obtain new approval', 409)
            identifier = uuid.uuid4().hex
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,NULL)',
                       (identifier, actor.agent, op.approval_id, canonical(assignment.model_dump()), 'assigned'))
            db.execute('UPDATE approvals SET used_by=? WHERE id=?', (identifier, op.approval_id))
            self.message(db, actor.agent, assignment.recipient, assignment.task_id, op.correlation_id, 'assignment',
                         {'assignment_id': identifier, 'assignment': assignment.model_dump()})
            return {'assignment_id': identifier, 'status': 'assigned'}
        row = db.execute('SELECT sender,approval_id,content,status,result FROM assignments WHERE id=?', (op.assignment_id,)).fetchone()
        require(row is not None, 'not_found', 'Assignment not found', 404)
        assignment = Assignment.model_validate_json(row[2])
        self.scope(actor, assignment.task_id)
        require(actor.agent in (row[0], assignment.recipient), 'forbidden', 'Assignment belongs to another agent', 403)
        if op.operation == 'inspect':
            return {'assignment_id': op.assignment_id, 'assignment': assignment.model_dump(), 'status': row[3],
                    'result': json.loads(row[4]) if row[4] else None}
        require(actor.role == 'worker', 'forbidden', 'Current editing worker role required', 403)
        require(actor.agent == assignment.recipient, 'forbidden', 'Only the assigned worker may act', 403)
        if op.operation == 'accept':
            require(row[3] == 'assigned', 'assignment', 'Assignment is not awaiting acceptance', 409)
            grant = db.execute('SELECT stamp,expires FROM approvals WHERE id=?', (row[1],)).fetchone()
            # A durable workflow receipt recovers a commit even if the approval subsequently expired.
            with self.bridge.locked():
                _, _, task = self.bridge.task(assignment.task_id)
                receipt = {'operation_id': op.assignment_id, 'agent': assignment.recipient}
                require(task.get('coordination_start') == receipt or grant[1] > now(), 'approval', 'Approval expired before acceptance', 409)
                self.bridge.start(assignment, op.assignment_id, grant[0])
            db.execute('UPDATE assignments SET status=? WHERE id=?', ('accepted', op.assignment_id))
            return {'assignment_id': op.assignment_id, 'status': 'accepted'}
        require(op.operation == 'result' and row[3] == 'accepted', 'assignment', 'Accept the assignment before reporting a result', 409)
        self.references(op.artifact_refs)
        result = {'outcome': op.outcome, 'summary': op.summary, 'artifact_refs': op.artifact_refs}
        db.execute('UPDATE assignments SET status=?,result=? WHERE id=?', ('reported', canonical(result), op.assignment_id))
        self.message(db, actor.agent, row[0], assignment.task_id, op.correlation_id, 'result',
                     {'assignment_id': op.assignment_id, **result})
        return {'assignment_id': op.assignment_id, 'status': 'reported'}
