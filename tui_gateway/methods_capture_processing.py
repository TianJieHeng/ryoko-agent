"""Owned transport adapters for explicit local capture processing and review."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _capture_processing_handler(model_name, operation):
    @_profile_scoped
    def handle(rid, params):
        from hermes_cli import capture_processing
        from tui_gateway.contracts import capture_processing as contracts
        def call(agent, db, request):
            return getattr(capture_processing, operation)(agent.runtime_context, db,
                **request.model_dump(exclude={"schema_version", "session_id"}))
        return _artifact_request(rid, params, getattr(contracts, model_name), call)
    return handle


_METHODS = {
    "runtime.capture.process": ("CaptureProcessParams", "process_capture"),
    "runtime.capture.inspect": ("CaptureParams", "inspect_capture"),
    "runtime.capture.search": ("CaptureSearchParams", "search_captures"),
    "runtime.capture.batch.preview": ("CaptureBatchParams", "preview_capture_batch"),
    "runtime.capture.batch.commit": ("CaptureBatchCommitParams", "commit_capture_batch"),
}
for _name, _spec in _METHODS.items():
    method(_name)(_capture_processing_handler(*_spec))


def register(server):
    bind_module(globals(), server)
