# Opt-in recurring scheduling for a stdio-owned profile

The stdio entrypoint already maintains the durable admission queue. To make its
profile's ordinary Ryoko cron ticker run without a separate gateway, an operator
may enable the following reviewed `config.yaml` setting:

```yaml
cron:
  stdio_scheduler:
    enabled: true
    interval_seconds: 60
```

It is disabled by default. The interval accepts integer seconds 1 through 300;
this is polling frequency, not an override of any task recurrence. No RPC or
browser field enables it. Existing profile cron jobs must be reviewed first:
this runs the same ordinary ticker, including those jobs.

The existing admission maintenance handle drives it, with no additional timer,
agent loop or external scheduler provider. A verified live gateway owner makes
stdio stand down. The ordinary profile `.tick.lock` serializes all ticker paths;
occurrence acceptance, command admission, deadline and budget debit remain an
atomic transaction. Durable queue launch and turn leases prevent duplicate work.
Shutdown fences new ticks before queue teardown. Pausing/revoking a schedule stops
future admissions; it never cancels an already claimed command.

On restart/offline recovery the immutable schedule controls missed-run behavior.
An unknown claimed occurrence stays unknown and pauses its schedule rather than
replaying. Execution requires an authenticated owned live session. A detached
transport keeps accepted queue references pending; delivery retries use the
outbox, never schedule admission.

The owned read-only `runtime.schedule.scheduler.status` RPC reports explicit
opt-in, observed live admission maintenance, actual shared-lock acquire/release or
contention, the last successful tick, another gateway's ownership, and shutdown.
`recurring_admission_ready` requires fresh real maintenance and a successful
locked tick; enabling config alone does not satisfy it. The status contains no
paths, PIDs, credentials or raw errors. It never starts a ticker or dispatches.
The existing ticker marker files remain diagnostic evidence; the RPC does not
accept a stale file as proof of a live process. No provider credentials or
external delivery authority are granted by enabling this lifecycle option.
