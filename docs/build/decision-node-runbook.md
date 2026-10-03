# Decision node qualification runbook (not executed)

## Safety and deployment ownership

The proposed Jetson Orin Nano 8 GB / JetPack 6.2 configuration is unverified.
No node was accessed, provisioned, scanned or benchmarked during this change.
Do not deploy this core handler as an unauthenticated standalone HTTP service.
Keep the actual LAYA serving adapter and deployment manifests outside core.
`TypedDecisionService` is a transport-neutral protocol handler for that adapter;
it is not a vendor implementation and has no chat/generative endpoint.

Before any operator-authorized deployment:

1. Verify hardware identity, available memory, OS/JetPack, supported serving
   binary/API, checkpoint architecture, license and offline artifact checksums
2. Pin model, calibration, service and complete registry digests as a rollback
   bundle. No online latest-version lookup at boot. Verify the adapter really
   loaded those artifact bytes, not merely that a file with that hash exists
3. Use an unprivileged dedicated service identity and private read-only model
   paths. Bind only a literal RFC1918 LAN interface. No public listener/tunnel,
   docs/OpenAPI route, generic execution endpoint or cohosted OCR/translation
4. Configure a host firewall allowlist, unique SSH keys and least-privilege
   operator access. Configure TLS 1.2+ and per-client mutual-TLS certificates,
   existing approved CA, IP SAN and exact server-certificate SHA-256 pin. Rotate
   credentials under a separate approved procedure; this code creates none
5. Bound HTTP workers, TLS-handshake/header/body read times, content length,
   service queue and deadline. The typed handler bounds inference admission but
   cannot cancel a hung vendor kernel; hung calls keep their slot until stopped.
   The client abandons them safely without replacement-thread growth
6. Disable access/body/exception-payload logs. Export metadata request counts,
   failure classes, queue depth and latency only. Confirm certificates, headers,
   user packets and classifier traces never appear in logs or crash dumps
7. Exercise synthetic/public shadow traffic, then verify rejected unauthenticated
   clients, wrong CA/pin, off-allowlist peers, public-interface scans, malformed
   distributions, stale digests, overload, node outage and rollback
8. Keep private-state transmission blocked. BE14 currently reports storage,
   encryption/key custody, deletion/retention and deployment qualification
   incomplete. Destination-bound private authorization is also not implemented.
   Neither `mode: shadow` nor a TLS certificate overrides those gates

The local TLS tests use newly generated disposable test certificates on loopback,
not live credentials. `synthetic_loopback: true` rejects all non-synthetic packets.
Production LAN transport never follows redirects, proxies or DNS; pin validation
occurs before sending any application body. `allowed_client_addresses` is a
manifest requirement and handler check; it does not install a firewall. The
service's `mutually_authenticated` argument must come from a validated TLS socket,
never an HTTP header or request body.

## Manifest and health contract

`NodeManifest` requires literal address/port, server certificate pin, explicit
client IPs, model/calibration/service/registry digests and bounded queue/request/
response sizes. Existing CA/client certificate/key paths are separate local TLS
configuration. Do not put key bytes in config.yaml, receipts or command output.

`GET /v1/health` requires mutual TLS and returns only schema version, exact loaded
bundle/registry digests, active queue/max queue, measured used/limit memory,
uptime, readiness and `hardware_verified: false`. The generic handler verifies
local model/calibration bytes but cannot attest a vendor kernel or Jetson model;
its hardware flag deliberately remains false. The separately reviewed adapter
must provide actual memory/resource counters. Fixture resource numbers establish
schema tests only, not memory requirements.

`POST /v1/decide` receives a single closed typed request. Extra endpoints and
private payloads are rejected in this release. The host owns finite HTTP admission;
handler owns finite inference admission. The adapter produces probabilities,
selected option and unclear, never prose or commands.

## Measurement procedure

The checked-in runner sends fixed synthetic texts only and makes no quality or
hardware qualification claim:

```sh
python evals/decisions/benchmark.py run --manifest /approved/node.json \
  --tls /approved/tls-paths.json --backend pytorch --rounds 100 > pytorch.json
python evals/decisions/benchmark.py run --manifest /approved/node-fp16.json \
  --tls /approved/tls-paths.json --backend tensorrt_fp16 --rounds 100 > fp16.json
python evals/decisions/benchmark.py compare pytorch.json fp16.json
```

Use an approved provisioned node and existing credentials only. Record actual
hardware/OS/serving versions, licensed artifact sources, adapter-loaded digests,
client network path, peak memory, temperatures/power mode and independent
hardware evidence digest alongside reports. Run during representative load.

The runner reports measured end-to-end p50/p95/max, raw samples, failure counts,
health and sequential-batch p50/p95 with warmups excluded. Sequential batches are
explicitly not server-native batching or GPU throughput claims. Comparisons
require matching model/calibration/registry/cases and report winner/distribution
parity; a faster mismatched answer is not acceptable. Quantized artifacts with a
different model hash need an explicit independently validated equivalence study,
not this exact-artifact comparison.

The design targets of 150 ms interactive p95 and one-second background batches
are proposed acceptance targets, not measurements from this implementation.
Local synthetic TLS measurements in test receipts are client/container loopback
observations only. Test resources, fitted arithmetic fixtures and CPU doubles
cannot promote a point. Benchmark public-domain holdouts and real task outcomes
separately before considering qualified promotion.
