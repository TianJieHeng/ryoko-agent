# Local agenda planning and candidate review

`runtime.commitment.decline` retains an exact human decision under owner/project scope and expected candidate revision. Reimport cannot overwrite it; later acceptance fails closed. Accepted obligations still use explicit sourced terminal transitions. No generic terminal reopening is introduced.

`runtime.agenda.plan` consumes an exact immutable availability artifact, IANA zone, exact participants, one to seven daily offset-qualified work windows, explicitly ordered work IDs/duration estimates, a buffer and a daily flexible-work capacity. It separates fixed intervals, proposed flexible blocks and overflow. All proposed work starts no earlier than the server planning time. Buffers separate blocks and fixed intervals; unsplittable work that cannot fit remains visible in overflow. Daily windows, timezone offsets and DST are validated.

The output retains source identity, snapshot age, stale status and explicit input order. It never changes a calendar, sends an invitation, creates an obligation or claims current provider availability. Live calendar/meeting sources and client acceptance remain separate gates.
