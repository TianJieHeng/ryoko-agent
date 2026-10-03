"""Bounded Dots-owned page and computer executors over a pinned stdio peer.

These contracts carry exact producer authority, never a client-selected URL,
credential, callback name or ambient execution capability.
"""
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictFloat

from .base import Params, Result
from .registry import method, server_request
from .runtime_v1 import RuntimeIdentifier, RuntimeSessionParams
from .runtime_results import Digest

Revision = Annotated[StrictInt, Field(ge=0)]
DotsAction = Literal["navigate", "read", "snapshot", "screenshot", "click", "type", "key", "scroll",
                     "files_list", "files_read", "files_write", "exec"]


class DotsRegistrationParams(RuntimeSessionParams):
    adapter_id: RuntimeIdentifier
    kind: Literal["page", "computer"]
    expected_revision: Revision | None = None
    revision: Revision
    enabled: bool
    project_ids: Annotated[list[RuntimeIdentifier], Field(max_length=100)] = []
    space_ids: Annotated[list[RuntimeIdentifier], Field(max_length=100)] = []
    actions: Annotated[list[DotsAction], Field(max_length=12)] = []


class DotsRegistrationResult(Result):
    adapter_id: str
    kind: Literal["page", "computer"]
    revision: int
    enabled: bool
    agent_id: str
    registered: Literal[True]


class DotsPageDocument(Params):
    title: Annotated[str, Field(min_length=1, max_length=256)]
    content: Annotated[str, Field(max_length=24000)]
    parent_id: RuntimeIdentifier | None
    archived: bool


class DotsPageProposal(Params):
    kind: Literal["page"] = "page"
    store_id: RuntimeIdentifier
    project_id: RuntimeIdentifier
    space_id: RuntimeIdentifier
    page_id: RuntimeIdentifier
    expected_head_version: Revision
    expected_grant_revision: Revision
    document: DotsPageDocument


class DotsEmptyInput(Params):
    pass


class DotsNavigateInput(Params):
    url: Annotated[str, Field(min_length=1, max_length=2048)]


class DotsClickInput(Params):
    ref: Annotated[str, Field(min_length=1, max_length=100)]
    snapshotId: Revision


class DotsTypeInput(DotsClickInput):
    text: Annotated[str, Field(max_length=16000)]
    submit: bool = False


class DotsKeyInput(Params):
    key: Annotated[str, Field(min_length=1, max_length=100)]


class DotsScrollInput(Params):
    deltaY: Annotated[StrictInt | StrictFloat, Field(ge=-10000, le=10000, allow_inf_nan=False)]


class DotsFilesListInput(Params):
    path: Annotated[str, Field(max_length=1024)] = ""


class DotsFilesReadInput(Params):
    path: Annotated[str, Field(min_length=1, max_length=1024)]


class DotsFilesWriteInput(DotsFilesReadInput):
    contents: Annotated[str, Field(max_length=24000)]
    append: bool = False


class DotsExecInput(Params):
    command: Annotated[str, Field(min_length=1, max_length=8000)]
    timeoutMs: Annotated[StrictInt, Field(ge=1000, le=60000)] = 30000


DotsComputerInput = (DotsEmptyInput | DotsNavigateInput | DotsClickInput | DotsTypeInput | DotsKeyInput
                     | DotsScrollInput | DotsFilesListInput | DotsFilesReadInput | DotsFilesWriteInput | DotsExecInput)


class DotsComputerProposal(Params):
    kind: Literal["computer"] = "computer"
    executor_id: RuntimeIdentifier
    expected_grant_revision: Revision
    expected_control_revision: Revision
    snapshot_id: Revision
    snapshot_sha256: Digest
    action: DotsAction
    input: DotsComputerInput


class DotsPagePrepareParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier
    proposal: DotsPageProposal


class DotsComputerPrepareParams(RuntimeSessionParams):
    command_id: RuntimeIdentifier
    proposal: DotsComputerProposal


class DotsPagePublishParams(DotsPagePrepareParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class DotsComputerExecuteParams(DotsComputerPrepareParams):
    approval_id: RuntimeIdentifier
    approval_digest: Digest


class DotsPreparedResult(Result):
    command_id: str
    run_id: str
    operation_id: str
    action_digest: Digest
    input_digest: Digest
    content_sha256: Digest
    approval_id: str
    approval_digest: Digest
    expires_at: float


class DotsEffectIdentity(Params):
    schema_version: Literal[1] = 1
    principal_id: RuntimeIdentifier
    profile_id: RuntimeIdentifier
    agent_id: RuntimeIdentifier
    runtime_session_id: RuntimeIdentifier
    run_id: RuntimeIdentifier
    operation_id: RuntimeIdentifier
    effect_id: RuntimeIdentifier
    approval_id: RuntimeIdentifier
    approval_digest: Digest
    action_digest: Digest
    input_digest: Digest
    policy_digest: Digest
    policy_version: str
    generation: Revision
    adapter_id: RuntimeIdentifier
    adapter_kind: Literal["page", "computer"]
    grant_revision: Revision
    scope_json: Annotated[str, Field(max_length=8192)]
    content_sha256: Digest
    content_size: Revision


class DotsDispatchRequest(Params):
    session_id: str
    identity: DotsEffectIdentity
    proposal: DotsPageProposal | DotsComputerProposal
    content_json: Annotated[str, Field(max_length=65536)]
    deadline_at: float


class DotsInspectRequest(Params):
    session_id: str
    identity: DotsEffectIdentity
    deadline_at: float


class DotsEffectReceipt(Result):
    identity: DotsEffectIdentity
    state: Literal["committed", "not_applied", "outcome_unknown"]
    receipt_id: RuntimeIdentifier | None = None
    content_sha256: Digest | None = None
    version: Revision | None = None
    result_sha256: Digest | None = None
    reason: Literal["committed", "conflict", "grant_revoked", "takeover", "stale_snapshot", "unavailable", "unknown"]


class DotsEffectResult(Result):
    effect_id: str
    operation_id: str
    state: Literal["prepared", "dispatched", "confirmed", "failed", "outcome_unknown", "reconciliation_required"]
    receipt: DotsEffectReceipt | None
    replay_permitted: Literal[False] = False


class DotsReconcileParams(RuntimeSessionParams):
    effect_id: RuntimeIdentifier


method("runtime.dots.register", params=DotsRegistrationParams, result=DotsRegistrationResult,
       doc="Register the owned current stdio peer's native adapter; does not provision credentials or replay effects.")
method("runtime.dots.page.prepare", params=DotsPagePrepareParams, result=DotsPreparedResult)
method("runtime.dots.page.publish", params=DotsPagePublishParams, result=DotsEffectResult)
method("runtime.dots.computer.prepare", params=DotsComputerPrepareParams, result=DotsPreparedResult)
method("runtime.dots.computer.execute", params=DotsComputerExecuteParams, result=DotsEffectResult)
method("runtime.dots.effect.reconcile", params=DotsReconcileParams, result=DotsEffectResult)
server_request("dots.effect.dispatch", params=DotsDispatchRequest, result=DotsEffectReceipt)
server_request("dots.effect.inspect", params=DotsInspectRequest, result=DotsEffectReceipt)


class DotsReadAuthority(Params):
    principal_id: RuntimeIdentifier
    profile_id: RuntimeIdentifier
    agent_id: RuntimeIdentifier
    runtime_session_id: RuntimeIdentifier
    run_id: RuntimeIdentifier
    policy_digest: Digest
    generation: Revision


class DotsPageReadScope(Params):
    store_id: RuntimeIdentifier
    project_id: RuntimeIdentifier
    space_id: RuntimeIdentifier
    page_id: RuntimeIdentifier
    expected_grant_revision: Revision
    version: Annotated[StrictInt, Field(ge=1)] | None = None


class DotsPageReadRequest(Params):
    session_id: str
    authority: DotsReadAuthority
    scope: DotsPageReadScope
    deadline_at: float


class DotsPageReadResult(Result):
    authority: DotsReadAuthority
    scope: DotsPageReadScope
    version: Annotated[StrictInt, Field(ge=1)]
    content_json: Annotated[str, Field(max_length=65536)]
    content_sha256: Digest


class DotsComputerObserveScope(Params):
    executor_id: RuntimeIdentifier
    expected_grant_revision: Revision
    action: Literal["snapshot", "read", "screenshot", "files_list", "files_read", "result"]
    input: DotsEmptyInput | DotsFilesListInput | DotsFilesReadInput = Field(default_factory=DotsEmptyInput)
    effect_id: RuntimeIdentifier | None = None


class DotsComputerObserveRequest(Params):
    session_id: str
    authority: DotsReadAuthority
    scope: DotsComputerObserveScope
    deadline_at: float


class DotsComputerObserveResult(Result):
    authority: DotsReadAuthority
    scope: DotsComputerObserveScope
    control_revision: Revision
    snapshot_id: Revision
    snapshot_sha256: Digest
    content_json: Annotated[str, Field(max_length=65536)]
    content_sha256: Digest


class DotsPageToolProposal(Params):
    request_id: RuntimeIdentifier
    proposal: DotsPageProposal


class DotsComputerToolProposal(Params):
    request_id: RuntimeIdentifier
    proposal: DotsComputerProposal


server_request("dots.page.read", params=DotsPageReadRequest, result=DotsPageReadResult)
server_request("dots.computer.observe", params=DotsComputerObserveRequest, result=DotsComputerObserveResult)


class DotsApprovalRequest(Params):
    session_id: str
    authority: DotsReadAuthority
    approval_id: RuntimeIdentifier
    approval_digest: Digest
    action_digest: Digest
    expires_at: float


class DotsApprovalResult(Result):
    approval_id: RuntimeIdentifier
    approval_digest: Digest
    choice: Literal["once", "deny"]


server_request("dots.approval", params=DotsApprovalRequest, result=DotsApprovalResult)
