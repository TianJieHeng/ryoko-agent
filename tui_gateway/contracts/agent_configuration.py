"""Owner controls may edit presentation and narrow configured authority only."""
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, StrictInt, model_validator
from .base import Params, Result
from .registry import method
from .runtime_v1 import RuntimeSessionParams

AgentID = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]
Revision = Annotated[StrictInt, Field(ge=1)]


class AgentEditableConfig(Params):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: Annotated[str, Field(min_length=1, max_length=200)]
    instructions: Annotated[str, Field(max_length=20000)] = ''
    research_allowed: bool = True
    memory_allowed: bool = True
    project_grants: Annotated[list[Annotated[str, Field(min_length=1, max_length=256)]], Field(max_length=100)] = Field(default_factory=list)
    default_project_id: Annotated[str, Field(min_length=1, max_length=256)] | None = None

    @model_validator(mode='after')
    def valid_scope(self):
        if not self.name.strip() or len(set(self.project_grants)) != len(self.project_grants):
            raise ValueError('Unique projects and a display name are required')
        if self.default_project_id is not None and self.default_project_id not in self.project_grants:
            raise ValueError('Default project must be explicitly granted')
        return self


class AgentConfigurationRecord(Result):
    agent_id: AgentID
    role: Literal['primary', 'specialist']
    memory_backend: Literal['personal_mcp', 'builtin']
    builtin_memory_namespace: str | None
    config: AgentEditableConfig
    revision: Revision
    archived: bool
    active_session_revision: int | None
    authority_revocation_revision: Annotated[StrictInt, Field(ge=0)]
    active_session_revision_revoked: bool
    activation: Literal['next_session'] = 'next_session'
    personal_memory_mutation_supported: Literal[False] = False


class AgentConfigurationResult(Result):
    agent: AgentConfigurationRecord


class AgentConfigurationList(Result):
    agents: list[AgentConfigurationRecord]
    activation: Literal['next_session'] = 'next_session'


class AgentConfigurationParams(RuntimeSessionParams):
    agent_id: AgentID


class AgentConfigurationCreateParams(RuntimeSessionParams):
    copy_from_agent_id: AgentID | None = None
    config: AgentEditableConfig


class AgentConfigurationUpdateParams(AgentConfigurationParams):
    expected_revision: Revision
    config: AgentEditableConfig


class AgentConfigurationArchiveParams(AgentConfigurationParams):
    expected_revision: Revision


method('runtime.agent.list', params=RuntimeSessionParams, result=AgentConfigurationList)
method('runtime.agent.get', params=AgentConfigurationParams, result=AgentConfigurationResult)
method('runtime.agent.create', params=AgentConfigurationCreateParams, result=AgentConfigurationResult)
method('runtime.agent.update', params=AgentConfigurationUpdateParams, result=AgentConfigurationResult)
method('runtime.agent.archive', params=AgentConfigurationArchiveParams, result=AgentConfigurationResult)


class AgentSessionWorkflowPin(Result):
    project_id: str
    workflow_id: str
    version: Revision
    sha256: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    delivery_revision: Revision
    workflow_state: Literal['draft', 'tested', 'approved', 'deprecated', 'revoked', 'unavailable']


class AgentSessionConfiguration(Result):
    agent_id: AgentID
    role: Literal['primary', 'specialist', 'child']
    memory_backend: Literal['personal_mcp', 'builtin']
    active_configuration_revision: int | None
    desired_configuration_revision: int | None
    archived: bool
    authority_revocation_revision: int
    authority_current: bool
    revocation_code: str | None
    startup_frozen: bool
    active_workflows: list[AgentSessionWorkflowPin]
    desired_workflows: list[AgentSessionWorkflowPin]
    activation: Literal['next_session']
    execution_authority: Literal[False]


method('runtime.agent.session.get', params=RuntimeSessionParams, result=AgentSessionConfiguration,
       doc='Inspect current-session frozen revisions and desired workflow pins without changing prompts or granting execution.')
