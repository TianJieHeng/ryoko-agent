# Daily conversation startup

The classic interactive CLI can default to a date-named conversation in the configured
personal memory harness. Enable per profile in `config.yaml`:

```yaml
memory:
  daily_conversation:
    enabled: true
    prefix: mobile-daily-
    timezone: America/New_York
```

The date is computed at each launch in the configured IANA timezone. An empty daily
timezone uses the profile's `timezone`, then the machine's local timezone. The source
comes from `memory.conversation_push.source`; capture must be enabled. Other profiles
remain disabled by default. Change config through Hermes's config writer/commands.

Plain `hermes` opens today's exact source/session ID, reusing its verified local
timeline or importing the harness's available archived windows. The startup line
shows the daily name. Explicit `--resume` and `--continue` take precedence; query,
one-shot, safe-mode, noninteractive and alternate TUI launches retain their usual behavior.
An already running instance of that thread is protected by normal session leases.

If absent, Hermes prepares an empty, named local session bound to the deterministic
remote ID. The harness rejects empty turn batches, so remote creation occurs on the
first actual message through the existing durable worker. No synthetic conversation
turn is inserted. A later morning producer using the same source/ID joins the same
thread. Repeated opening before the first message reuses the same local session.

Archived windows can omit recent messages. A thread with no sealed windows opens with
empty local context and an explicit notice. Saved local history is retained, with a
warning when remote counts indicate additional messages; remote text is never silently
merged into an existing timeline. A failed read stops daily startup instead of treating
an outage as proof of absence. Retry or use an explicit resume selection.

Disable with `hermes config set memory.daily_conversation.enabled false` in the same
profile. Existing bindings and their same-thread delivery remain intact. No harness
deployment or worker restart is required.
