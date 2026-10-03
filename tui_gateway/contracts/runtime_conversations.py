"""Trusted single-owner stdio conversation API. No caller-selected authority."""
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictInt, field_validator

from .base import Params, Result
from .registry import method
from .runtime_v1 import RuntimeCommandReceiptResult, RuntimeIdentifier


class RuntimeConversationParams(Params):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


class RuntimeConversationRefParams(RuntimeConversationParams):
    conversation_id: RuntimeIdentifier


class RuntimeConversationIdentity(Result):
    principal_id: RuntimeIdentifier
    profile_id: RuntimeIdentifier
    agent_id: RuntimeIdentifier
    policy_digest: str
    config_digest: str
    role: Literal["primary", "specialist"]
    memory_backend: Literal["personal_mcp", "builtin"]


class RuntimeConversationCapabilities(Result):
    schema_version: Literal[1]
    authority: Literal["trusted_stdio_owner"]
    owner_scope: Literal["principal_profile_agent_home"]
    identity: RuntimeConversationIdentity
    methods: list[str]
    max_page: int
    max_text_chunk_chars: int
    max_page_text_bytes: int
    transcript_format: Literal["safe_transcript_v1"]
    command_message_linkage: Literal["explicit"]
    restore_supported: Literal[False]


class RuntimeConversation(Result):
    conversation_id: str
    agent_id: RuntimeIdentifier
    title: str
    archived: bool
    revision: int
    created_at: float
    updated_at: float
    source: Literal["web"]


class RuntimeConversationOperationParams(RuntimeConversationParams):
    agent_id: RuntimeIdentifier | None = None
    idempotency_key: RuntimeIdentifier


class RuntimeConversationOperationResult(Result):
    schema_version: Literal[1]
    found: bool
    idempotency_key: str
    operation: Literal["create", "rename", "archive"] | None
    conversation: RuntimeConversation | None


class RuntimeConversationCreateParams(RuntimeConversationParams):
    agent_id: RuntimeIdentifier | None = None
    idempotency_key: RuntimeIdentifier
    title: Annotated[str, Field(max_length=200)] = ""


class RuntimeConversationCreateResult(Result):
    schema_version: Literal[1]
    conversation: RuntimeConversation
    created: bool


class RuntimeConversationListParams(RuntimeConversationParams):
    agent_id: RuntimeIdentifier | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50
    cursor: Annotated[str, Field(max_length=2048)] | None = None
    archived: bool = False
    query: Annotated[str, Field(max_length=200)] = ""


class RuntimeConversationListResult(Result):
    schema_version: Literal[1]
    conversations: list[RuntimeConversation]
    next_cursor: str | None
    has_more: bool


class RuntimeConversationBindResult(Result):
    schema_version: Literal[1]
    conversation: RuntimeConversation
    session_id: str
    readiness: Literal["building", "ready", "failed"]
    failure_code: Literal["agent_build_failed", "identity_mismatch"] | None


class RuntimeConversationMutationParams(RuntimeConversationRefParams):
    idempotency_key: RuntimeIdentifier
    expected_revision: Annotated[StrictInt, Field(ge=1)]


class RuntimeConversationRenameParams(RuntimeConversationMutationParams):
    title: Annotated[str, Field(min_length=1, max_length=200)]


class RuntimeConversationArchiveParams(RuntimeConversationMutationParams):
    archived: bool


class RuntimeConversationResult(Result):
    schema_version: Literal[1]
    conversation: RuntimeConversation


class RuntimeConversationHistoryParams(RuntimeConversationRefParams):
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50
    cursor: Annotated[str, Field(max_length=2048)] | None = None


class RuntimeConversationCommandReceiptParams(RuntimeConversationRefParams):
    command_id: RuntimeIdentifier
    message_limit: Annotated[StrictInt, Field(ge=1, le=100)] = 100
    message_cursor: Annotated[str, Field(max_length=2048)] | None = None


class RuntimeConversationTextChunk(Result):
    message_id: str
    physical_session_id: str
    role: Literal["user", "assistant"]
    text: str
    text_offset: int = Field(description="UTF-8 byte offset in the original safe text before control-character sanitization")
    next_text_offset: int = Field(description="Exclusive UTF-8 source byte end offset before control-character sanitization")
    text_complete: bool
    text_sanitized: bool
    non_text_omitted: bool
    timestamp: float
    committed: Literal[True]
    command_id: str | None


class RuntimeConversationHistoryResult(Result):
    schema_version: Literal[1]
    conversation_id: str
    format: Literal["safe_transcript_v1"]
    messages: list[RuntimeConversationTextChunk]
    lineage: list[str]
    snapshot_max_row_id: int
    next_cursor: str | None
    has_more: bool


method("runtime.conversation.capabilities", params=RuntimeConversationParams, result=RuntimeConversationCapabilities,
       doc="Discover the owner-scoped API. Only the server-owned launch-profile stdio pipe is supported.")
method("runtime.conversation.create", params=RuntimeConversationCreateParams, result=RuntimeConversationCreateResult,
       doc="Atomically persist a canonical conversation and an owner-scoped durable idempotency receipt.")
method("runtime.conversation.list", params=RuntimeConversationListParams, result=RuntimeConversationListResult,
       doc="Bounded owner-only title search/list, ordered by stable creation key.")
method("runtime.conversation.bind", params=RuntimeConversationRefParams, result=RuntimeConversationBindResult,
       doc="Authorize before initializing/reusing a live session; poll readiness without resubmitting commands.")
method("runtime.conversation.rename", params=RuntimeConversationRenameParams, result=RuntimeConversationResult,
       doc="Idempotent metadata-revision-checked rename; a retry returns the original mutation receipt.")
method("runtime.conversation.archive", params=RuntimeConversationArchiveParams, result=RuntimeConversationResult,
       doc="Idempotent metadata-revision-checked archive/restore; does not cancel running work.")
method("runtime.conversation.history", params=RuntimeConversationHistoryParams, result=RuntimeConversationHistoryResult,
       doc="Committed human/assistant text only, stable message IDs, compression lineage, bounded text chunks. Append watermark, not immutable edit snapshot.")
method("runtime.conversation.export", params=RuntimeConversationHistoryParams, result=RuntimeConversationHistoryResult,
       doc="Same paged safe transcript as history; concatenate text chunks by message_id and text_offset. Not a runtime backup or import format.")

method("runtime.conversation.operation.get", params=RuntimeConversationOperationParams, result=RuntimeConversationOperationResult,
       doc="Read the original owner-scoped operation receipt or found=false. Never create, rename, archive, bind or queue work.")

method("runtime.conversation.command.receipt", params=RuntimeConversationCommandReceiptParams, result=RuntimeCommandReceiptResult,
       doc="Read an owned canonical conversation's durable command receipt and bounded message links without binding a live session or constructing a provider. Never requeue or claim work.")
