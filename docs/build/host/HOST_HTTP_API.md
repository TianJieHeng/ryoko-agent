# Independent Ryoko-Dots host endpoint

The existing `ryoko` Hermes profile runs on the physical host, using the local
terminal backend in `~/Desktop/rhermes/ryoko-agent`. It is independent of the
Hermes build worker inside Dots, the Dots API and Agent Chat bridge.

## Connection contract

- User service: `hermes-host-api.service`, enabled with user lingering.
- HTTP base: `http://127.0.0.1:8642`.
- Public liveness: `GET /api/health`.
- Authenticated session listing: `GET /api/sessions`.
- Native agent protocol: `ws://127.0.0.1:8642/api/ws` (JSON-RPC).
- Private credential: `~/.config/portal-dashboard/host-hermes.json` (`token`).
- Service credential environment: `~/.config/portal-dashboard/host-hermes.env`
  (`HERMES_DASHBOARD_SESSION_TOKEN`). Both files are mode 0600, outside Git.

For HTTP requests use `Authorization: Bearer <token>`. The loopback WebSocket
uses `?token=<url-encoded-token>`; keep connection URLs out of logs. The Portal
server must own this connection and secret, never the browser.

This is Hermes's **native HTTP/WebSocket backend**, not its OpenAI-compatible
`/v1/chat/completions` API. This fork's strict identity runtime refuses the latter
with `runtime_delivery_transport_unsupported`. No runtime guard was relaxed.
The service launches:

```sh
hermes --profile ryoko serve --isolated --host 127.0.0.1 --port 8642
```

The original profile config and `.env` were restored after testing the incompatible
API-server path; pre-change copies remain in `backups/host-api-*` under the profile.
Provider, model, identity policy and terminal permissions remain the existing
host agent's. No separate gateway multiplexing configuration is required.

## Native chat sequence for Portal integration

1. Authenticate the WebSocket; await `event` with `params.type = gateway.ready`.
2. Call `session.create` with an idempotency key, or `session.resume` for a stored
   conversation. Keep both runtime `session_id` and `stored_session_id`.
3. **Await `session.info` for that session before sending the first prompt.**
   Creation returns a lazy session. Testing exposed a race in this revision:
   an immediate `prompt.submit` can persist its user row before the strict
   identity is constructed, causing a legacy-session identity refusal. Waiting
   for the agent-ready information avoids this; do not retry a failed unbound
   session by weakening identity validation.
4. Call `prompt.submit` with `{session_id, text}`. Observe `message.delta`,
   `message.complete`, errors and runtime-result notifications. RPC acceptance
   alone is not proof of successful inference.
5. Implement `client.capabilities {server_requests:true}` only when the client
   handles the server's approval/clarification requests. Preserve existing
   approvals; never auto-approve recovery commands.
6. Use native runtime result/replay APIs for reconnect recovery. Contracts live
   in `tui_gateway/contracts/` and `apps/shared/src/gateway-contract.generated.ts`.

## Operation and boundaries

```sh
systemctl --user status hermes-host-api.service
systemctl --user restart hermes-host-api.service
systemctl --user stop hermes-host-api.service
```

The checked-in sibling service template has no Dots dependency, runs in its own
systemd cgroup, restarts on failure and bounds memory/CPU/tasks. It intentionally
retains host terminal access for authorized process recovery. Startup can take
30–60 seconds; verify `/api/health` rather than treating process existence as
readiness. The memory-harness outbox service remains separate.

The origin listener is loopback-only. Remote access now uses the dedicated
Cloudflare tunnel described below. A host agent
can help recover a stuck Dots environment, but not a powered-off host or broken OS.

Portal Device Agent enrollment and its native Hermes adapter are still pending.
Name this host card `Ryoko-Dots`; keep build workers on Build Agents. Do not wire
this recovery card to the Dots API.

## Remote HTTPS / Cloudflare tunnel

Remote base: **https://hermes-dots.ryoko.okinawa**.
Native WebSocket: **wss://hermes-dots.ryoko.okinawa/api/ws**.
The same private credential and native session-ready sequence above apply.
This is an API endpoint; visiting `/` intentionally returns 404.

Tunnel `ryoko-host-hermes` (`d2b8f546-6d2e-41b5-bf4c-8e4887519eb7`)
runs as independent enabled user service `hermes-host-tunnel.service`. Its private
configuration is `~/.config/hermes-host/cloudflare/config.yml`; credentials are
in `~/.cloudflared/<tunnel-id>.json`. No Dots service dependency exists.

Ingress permits only exact `/api/health`, `/api/sessions` and `/api/ws` paths.
All other paths and hostnames receive 404, including the local-only root page
which embeds a desktop bootstrap credential. Never replace the restricted
path with a catch-all proxy. `/api/health` is public liveness; sessions require
bearer authentication, and WebSocket upgrades require the token. The tunnel
rewrites the origin Host header to `127.0.0.1:8642` so Hermes's host validation
remains enabled. Server-to-server clients should omit browser Origin headers;
a future direct-browser client needs a reviewed origin/auth setup.

Cloudflare terminates HTTPS; the connector reaches only the loopback origin.
Keep tokens in the Portal backend, use HTTPS/WSS exclusively, and do not log
WebSocket query strings. This deployment uses Hermes token authentication,
not a separate Cloudflare Access login. The API does not become a public UI.

```sh
systemctl --user status hermes-host-tunnel.service
systemctl --user restart hermes-host-tunnel.service
systemctl --user stop hermes-host-tunnel.service
curl https://hermes-dots.ryoko.okinawa/api/health
```

To remove remote access, stop/disable the tunnel service and remove its DNS
route/tunnel in Cloudflare; the independent local Hermes service can stay up.
Templates: `hermes-host-tunnel.service`, `cloudflare-host.yml.example`.
Ingress setup follows the [Cloudflare local tunnel configuration contract](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/).
