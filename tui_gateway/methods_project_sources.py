"""Owned typed controls for explicit project source metadata and safe resume joins."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped


def _source_public(row, model_name):
    from tui_gateway.contracts import project_sources as contracts
    model = getattr(contracts, model_name)
    return {key: row[key] for key in model.model_fields}


def _project_source_operation(agent, db, request, *, service, operation, result_key, model_name, many):
    from agent import evidence_ledger
    from hermes_cli import project_sources
    services = {"sources": project_sources, "evidence": evidence_ledger}
    arguments = request.model_dump(exclude={"schema_version", "session_id"})
    result = getattr(services[service], operation)(agent.runtime_context, db, **arguments)
    if many:
        return {result_key: [_source_public(row, model_name) for row in result], "limit": request.limit,
                "limit_reached": len(result) >= request.limit, "complete": False}
    if result_key is not None:
        return {result_key: _source_public(result, model_name)}
    return result


def _project_source_handler(params_model, service, operation, result_key=None, model_name=None, many=False):
    @_profile_scoped
    def handle(rid, params):
        from tui_gateway.contracts import project_sources as contracts
        return _artifact_request(rid, params, getattr(contracts, params_model),
            lambda agent, db, request: _project_source_operation(agent, db, request, service=service,
                operation=operation, result_key=result_key, model_name=model_name, many=many))
    return handle


_SOURCE_METHODS = {
    "runtime.capture.create": ("CaptureCreateParams", "sources", "create_capture", "capture", "CaptureRecord"),
    "runtime.capture.get": ("CaptureParams", "sources", "get_capture", "capture", "CaptureRecord"),
    "runtime.capture.list": ("SourceListParams", "sources", "list_captures", "captures", "CaptureRecord", True),
    "runtime.capture.file": ("CaptureFileParams", "sources", "file_capture", "capture", "CaptureRecord"),
    "runtime.capture.extraction.record": ("CaptureExtractionParams", "sources", "record_capture_extraction", "capture", "CaptureRecord"),
    "runtime.capture.read": ("CaptureReadParams", "sources", "read_capture"),
    "runtime.capture.duplicates": ("SourceListParams", "sources", "propose_capture_duplicates"),
    "runtime.template.create": ("TemplateCreateParams", "sources", "create_template", "template", "TemplateRecord"),
    "runtime.template.get": ("TemplateParams", "sources", "get_template", "template", "TemplateRecord"),
    "runtime.template.list": ("SourceListParams", "sources", "list_templates", "templates", "TemplateRecord", True),
    "runtime.evidence.create": ("EvidenceCreateParams", "evidence", "create_evidence_anchor", "evidence", "EvidenceRecord"),
    "runtime.evidence.get": ("EvidenceParams", "evidence", "get_evidence_anchor", "evidence", "EvidenceRecord"),
    "runtime.evidence.list": ("SourceListParams", "evidence", "list_evidence_anchors", "evidence", "EvidenceRecord", True),
    "runtime.resume.get": ("ProjectResumeParams", "evidence", "assemble_resume"),
}
for _source_method, _source_specification in _SOURCE_METHODS.items():
    method(_source_method)(_project_source_handler(*_source_specification))
del _source_method, _source_specification


def register(server):
    bind_module(globals(), server)
