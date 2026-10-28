"""Versioned strict JSON contracts shared by HTTP, local approvals, and MCP."""
from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

AgentId = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_-]{0,31}$')]
TaskId = Annotated[str, StringConstraints(pattern=r'^T[0-9]{3,}$')]
Identifier = Annotated[str, StringConstraints(pattern=r'^[a-zA-Z0-9_-]{1,80}$')]
Text = Annotated[str, StringConstraints(min_length=1, max_length=12000)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Principal(Model):
    agent: AgentId
    role: Literal['orchestrator', 'worker', 'reviewer']
    token_env: Annotated[str, StringConstraints(pattern=r'^[A-Z][A-Z0-9_]{2,80}$')]
    tasks: list[TaskId] = Field(max_length=1000)


class Settings(Model):
    version: Literal['1'] = '1'
    principals: list[Principal] = Field(min_length=1, max_length=100)


class Assignment(Model):
    task_id: TaskId
    recipient: AgentId
    paths: list[Text] = Field(min_length=1, max_length=100)
    acceptance_criteria: list[Text] = Field(min_length=1, max_length=100)
    worktree: Text
    branch: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class BaseOperation(Model):
    version: Literal['1'] = '1'
    request_id: Identifier
    correlation_id: Identifier


class Register(BaseOperation):
    operation: Literal['register']
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]


class Send(BaseOperation):
    operation: Literal['send']
    recipient: AgentId
    task_id: TaskId | None = None
    kind: Literal['question', 'finding', 'discussion']
    body: Text
    artifact_refs: list[Text] = Field(default_factory=list, max_length=50)


class Inbox(BaseOperation):
    operation: Literal['inbox']
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class Acknowledge(BaseOperation):
    operation: Literal['acknowledge']
    message_id: Identifier


class Assign(BaseOperation):
    operation: Literal['assign']
    approval_id: Identifier
    assignment: Assignment


class Accept(BaseOperation):
    operation: Literal['accept']
    assignment_id: Identifier


class Result(BaseOperation):
    operation: Literal['result']
    assignment_id: Identifier
    outcome: Literal['completed', 'needs_input', 'failed']
    summary: Text
    artifact_refs: list[Text] = Field(default_factory=list, max_length=50)


class Inspect(BaseOperation):
    operation: Literal['inspect']
    assignment_id: Identifier


class WorkflowStatus(BaseOperation):
    operation: Literal['workflow_status']
    task_id: TaskId


Operation = Annotated[Union[Register, Send, Inbox, Acknowledge, Assign, Accept, Result, Inspect, WorkflowStatus], Field(discriminator='operation')]
OPERATIONS = TypeAdapter(Operation)
SCHEMA = OPERATIONS.json_schema()
