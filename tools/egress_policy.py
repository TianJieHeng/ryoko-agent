"""BE05 exact outbound recipients and the certified HTTP transport boundary.

A plan is authority, not a networking sandbox or a spending allowance. Only the
adapters which install this boundary can use its grants; opaque adapters fail
closed. DNS names identify a recipient, never evidence of local-only execution.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import ip_address
import re
from urllib.parse import urlsplit, urlunsplit

PURPOSES = frozenset({"main_model", "aux_model", "memory", "embeddings", "mcp",
                      "browser_tool", "message_delivery", "telemetry", "provisioning",
                      "training", "subprocess", "connected_source", "decision_inference"})
TRANSPORTS = frozenset({"httpx", "subprocess", "browser", "opaque"})


class EgressDenied(PermissionError):
    """Safe reason code only; never includes credentials, payloads or endpoint URLs."""


def _url(value: object) -> tuple[str, str, int, str]:
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise EgressDenied("recipient_endpoint_invalid")
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise EgressDenied("recipient_endpoint_invalid") from exc
    if (parts.scheme not in {"http", "https"} or not host or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or "\\" in value
            or "%" in parts.netloc or host.endswith(".") or port == 0):
        raise EgressDenied("recipient_endpoint_invalid")
    path = parts.path or "/"
    # Avoid ambiguous gateway decoding, dot-segment redirects and prefix confusion.
    if "%" in path or any(piece in {".", ".."} for piece in path.split("/")) or "//" in path:
        raise EgressDenied("recipient_endpoint_invalid")
    return parts.scheme, host.lower(), port or (443 if parts.scheme == "https" else 80), path


def canonical_endpoint(value: object) -> str:
    scheme, host, port, path = _url(value)
    host = f"[{host}]" if ":" in host else host
    authority = host if port == (443 if scheme == "https" else 80) else f"{host}:{port}"
    return urlunsplit((scheme, authority, path.rstrip("/") or "/", "", ""))


@dataclass(frozen=True)
class RecipientGrant:
    recipient_id: str
    purpose: str
    endpoint: str
    transport: str

    def __post_init__(self):
        if not isinstance(self.recipient_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.recipient_id) is None:
            raise EgressDenied("recipient_id_invalid")
        if self.purpose not in PURPOSES or self.transport not in TRANSPORTS:
            raise EgressDenied("recipient_route_invalid")
        object.__setattr__(self, "endpoint", canonical_endpoint(self.endpoint))

    def to_record(self):
        return {"recipient_id": self.recipient_id, "purpose": self.purpose,
                "endpoint": self.endpoint, "transport": self.transport}

    def admits(self, url: str) -> bool:
        expected, actual = _url(self.endpoint), _url(url)
        root = expected[3].rstrip("/")
        return actual[:3] == expected[:3] and (actual[3] == root or actual[3].startswith(root + "/"))


@dataclass(frozen=True)
class RecipientPlan:
    schema_version: int
    envelope: str
    grants: tuple[RecipientGrant, ...]

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise EgressDenied("recipient_plan_version_unsupported")
        if self.envelope not in {"declared", "local_only"}:
            raise EgressDenied("recipient_envelope_invalid")
        if not isinstance(self.grants, (tuple, list)) or any(not isinstance(g, RecipientGrant) for g in self.grants):
            raise EgressDenied("recipient_grants_invalid")
        grants = tuple(self.grants)
        if len(set(grants)) != len(grants):
            raise EgressDenied("recipient_grants_duplicate")
        keys = [(g.purpose, g.endpoint, g.transport) for g in grants]
        if len(set(keys)) != len(keys):
            raise EgressDenied("recipient_grants_ambiguous")
        if self.envelope == "local_only":
            for grant in grants:
                try:
                    local = ip_address(_url(grant.endpoint)[1]).is_loopback
                except ValueError:
                    local = False
                if not local:
                    raise EgressDenied("local_only_requires_literal_loopback_recipient")
        object.__setattr__(self, "grants", tuple(sorted(grants, key=lambda g: (g.purpose, g.recipient_id, g.endpoint, g.transport))))

    @classmethod
    def from_record(cls, record):
        if isinstance(record, cls):
            return record
        if not isinstance(record, Mapping) or set(record) != {"schema_version", "envelope", "grants"}:
            raise EgressDenied("recipient_plan_invalid")
        if not isinstance(record["grants"], (tuple, list)):
            raise EgressDenied("recipient_grants_invalid")
        grants = []
        for raw in record["grants"]:
            if not isinstance(raw, Mapping) or set(raw) != {"recipient_id", "purpose", "endpoint", "transport"}:
                raise EgressDenied("recipient_grant_invalid")
            grants.append(RecipientGrant(**raw))
        return cls(record["schema_version"], record["envelope"], tuple(grants))

    def to_record(self, *, redacted=False):
        if redacted:
            return {"schema_version": 1, "envelope": self.envelope,
                    "purposes": sorted({g.purpose for g in self.grants}), "recipient_count": len(self.grants)}
        return {"schema_version": 1, "envelope": self.envelope, "grants": [g.to_record() for g in self.grants]}


def intersect_recipient_plans(parent, ceiling):
    # None is an uncertified legacy policy, never authority inherited by a child.
    if parent is None and ceiling is None:
        return None
    envelope = "local_only" if any(p is not None and p.envelope == "local_only" for p in (parent, ceiling)) else "declared"
    grants = tuple(g for g in ceiling.grants if g in parent.grants) if parent and ceiling else ()
    return RecipientPlan(1, envelope, grants)


def _context(*, require_run=False):
    from tools.capability_broker import require_live_policy
    return require_live_policy(require_run=require_run)


def policy_active() -> bool:
    from agent.runtime_context import current_agent_context
    context = current_agent_context()
    if context is None:
        from agent.identity_lifecycle import strict_identity_enabled
        if strict_identity_enabled():
            raise EgressDenied("recipient_identity_required")
        return False
    return True


@dataclass(frozen=True)
class RecipientAuthorization:
    context: object
    grant: RecipientGrant
    require_run: bool = True

    def check_url(self, url):
        if self.grant.purpose == "connected_source":
            from tools.connectors.source_reads import require_source_read
            require_source_read()
            context = _context(require_run=False)
        else:
            context = _context(require_run=self.require_run or self.grant.purpose != "mcp")
        if context != self.context:
            raise EgressDenied("recipient_authority_changed")
        plan = getattr(context.policy, "recipient_plan", None) if context is not None else None
        if plan is None or self.grant not in plan.grants:
            raise EgressDenied("recipient_not_granted")
        if not self.grant.admits(str(url)):
            raise EgressDenied("recipient_endpoint_changed")

    def request_hook(self, request):
        _check_request(self, request)

    async def async_request_hook(self, request):
        _check_request(self, request)


def prepare_recipient(purpose, endpoint, *, recipient_id=None, transport="httpx", require_run=True):
    """Resolve an explicit configured base endpoint; never guess or choose fallback."""
    if purpose == "connected_source":
        from tools.connectors.source_reads import require_source_read
        require_source_read()
    if not policy_active():
        return None
    context = _context(require_run=False)
    if getattr(context.policy, "recipient_plan", None) is None:
        raise EgressDenied("recipient_plan_required")
    if transport != "httpx":
        raise EgressDenied("recipient_transport_unsupported")
    canonical = canonical_endpoint(endpoint)
    matches = [g for g in context.policy.recipient_plan.grants
               if g.purpose == purpose and g.endpoint == canonical and g.transport == transport
               and (recipient_id is None or g.recipient_id == recipient_id)]
    if len(matches) != 1:
        raise EgressDenied("recipient_not_granted")
    return RecipientAuthorization(context, matches[0], require_run or purpose != "mcp")


def _check_request(authorization, request):
    authorization.check_url(request.url)
    # Host routing is part of recipient identity, not an arbitrary custom header.
    if request.headers.get("host", "").lower() != request.url.netloc.decode("ascii").lower():
        raise EgressDenied("recipient_host_header_changed")
    if authorization.grant.purpose in {"main_model", "aux_model"}:
        expected = authorization.grant.endpoint.rstrip("/") + "/chat/completions"
        if str(request.url) != expected or request.method != "POST":
            raise EgressDenied("recipient_model_operation_unsupported")
    if authorization.grant.purpose == "decision_inference":
        expected = authorization.grant.endpoint
        method = "GET" if _url(expected)[3] == "/health" else "POST"
        if str(request.url) != expected or request.method != method:
            raise EgressDenied("recipient_decision_operation_unsupported")


@lru_cache(maxsize=1)
def _transport_types():
    import httpx

    class GuardedTransport(httpx.BaseTransport):
        def __init__(self, transport, authorization):
            self.authorization = authorization
            self._inner = transport

        @property
        def _pool(self):
            return getattr(self._inner, "_pool", None)

        def handle_request(self, request):
            _check_request(self.authorization, request)
            return self._inner.handle_request(request)

        def close(self):
            return self._inner.close()

    class GuardedAsyncTransport(httpx.AsyncBaseTransport):
        def __init__(self, transport, authorization):
            self.authorization = authorization
            self._inner = transport

        @property
        def _pool(self):
            return getattr(self._inner, "_pool", None)

        async def handle_async_request(self, request):
            _check_request(self.authorization, request)
            return await self._inner.handle_async_request(request)

        async def aclose(self):
            return await self._inner.aclose()

    return GuardedTransport, GuardedAsyncTransport


def wrap_httpx_transport(transport, authorization, *, async_mode=False):
    """Check at actual transport dispatch, including SDK retries and every redirect."""
    if authorization is None:
        return transport
    return _transport_types()[int(async_mode)](transport, authorization)


def unwrap_httpx_transport(transport):
    """Retain the existing shared-pool cancellation owner beneath our guard."""
    while type(transport) in _transport_types():
        transport = transport._inner
    return transport


def _check_provider_transports(http_client, authorization):
    import httpx
    if type(http_client) not in {httpx.Client, httpx.AsyncClient} or http_client.trust_env:
        raise EgressDenied("recipient_provider_transport_unprotected")
    transports = [http_client._transport, *(t for t in http_client._mounts.values() if t is not None)]
    if any(type(t) not in _transport_types() or t.authorization != authorization for t in transports):
        raise EgressDenied("recipient_provider_transport_unprotected")


def assert_provider_client(client, *, purpose, require_run=True):
    if not policy_active():
        return
    import openai
    if type(client) not in {openai.OpenAI, openai.AsyncOpenAI}:
        raise EgressDenied("recipient_provider_adapter_unsupported")
    authorization = getattr(getattr(client, "_client", None), "_hermes_recipient_authorization", None)
    if not isinstance(authorization, RecipientAuthorization) or authorization.grant.purpose != purpose:
        raise EgressDenied("recipient_provider_transport_unprotected")
    _check_provider_transports(client._client, authorization)
    if require_run:
        authorization.check_url(client.base_url)
    else:
        context = _context(require_run=False)
        if (authorization.context != context or authorization.grant not in context.policy.recipient_plan.grants
                or not authorization.grant.admits(str(client.base_url))):
            raise EgressDenied("recipient_authority_changed")


def reject_unsupported_route(route):
    if policy_active():
        raise EgressDenied("recipient_route_unsupported")


def build_model_client(options, *, purpose, async_mode=False):
    """The first certified model route is an explicit OpenAI chat HTTP endpoint.

    No plugin constructor, implicit provider URL, OAuth refresh, ambient proxy or
    custom transport is consulted. Budget and failover policies remain separate.
    """
    from agent.process_bootstrap import build_keepalive_http_client
    import openai
    from agent.ssl_verify import resolve_httpx_verify

    options = dict(options)
    allowed = {"api_key", "base_url", "default_headers", "timeout", "max_retries",
               "organization", "project", "ssl_ca_cert", "ssl_verify"}
    if set(options) - allowed or not isinstance(options.get("api_key"), str):
        raise EgressDenied("recipient_provider_options_unsupported")
    endpoint = options.get("base_url")
    prepare_recipient(purpose, endpoint)
    verify = resolve_httpx_verify(ca_bundle=options.pop("ssl_ca_cert", None),
                                 ssl_verify=options.pop("ssl_verify", None), base_url=endpoint)
    options["http_client"] = build_keepalive_http_client(endpoint, async_mode=async_mode,
                                                       verify=verify, egress_purpose=purpose)
    options["max_retries"] = 0
    constructor = openai.AsyncOpenAI if async_mode else openai.OpenAI
    return constructor(**options)


def resolve_auxiliary_client(*, provider, model, base_url, api_key, api_mode, main_runtime, async_mode):
    """No discovery, profile plugin factory or credential refresh on strict routes."""
    runtime = main_runtime if isinstance(main_runtime, Mapping) else {}
    provider = provider or "auto"
    if provider not in {"auto", "openai", "custom"}:
        raise EgressDenied("recipient_provider_adapter_unsupported")
    if provider == "auto":
        if runtime.get("provider") not in {"openai", "custom"}:
            raise EgressDenied("recipient_auxiliary_route_required")
        provider = runtime["provider"]
    endpoint = base_url or runtime.get("base_url")
    mode = api_mode or runtime.get("api_mode") or "chat_completions"
    if mode != "chat_completions":
        raise EgressDenied("recipient_provider_adapter_unsupported")
    prepare_recipient("aux_model", endpoint)
    chosen_model = model or runtime.get("model")
    if not isinstance(chosen_model, str) or not chosen_model:
        raise EgressDenied("recipient_auxiliary_model_required")
    credential = api_key or runtime.get("api_key")
    if credential is None and provider == "openai":
        from agent.secret_scope import get_secret
        credential = get_secret("OPENAI_API_KEY")
    if not isinstance(credential, str) or not credential:
        raise EgressDenied("recipient_auxiliary_credential_required")
    return build_model_client({"base_url": endpoint, "api_key": credential},
                              purpose="aux_model", async_mode=async_mode), chosen_model
