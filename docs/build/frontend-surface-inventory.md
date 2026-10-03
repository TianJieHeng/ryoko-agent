# Runtime frontend boundary (FE00)

The backend generated `RpcMethods` contract is the only wire authority. The shared
`RuntimeControl` consumes capabilities, a durable snapshot and bounded event replay;
it owns no task, memory, policy or scheduling database. It is presentation state.

| Surface | Existing authority/entry point | New control boundary |
|---|---|---|
| Classic CLI | Central `CommandDef` and slash workers | Existing commands remain; no duplicate engine |
| Ink TUI | `createSlashHandler` and owned gateway session | Concise runtime inspectors on the same transport |
| Dashboard | Actual Ink child through `ChatPage` PTY | Inherits TUI controls; never creates a second chat |
| Electron | `requestGateway`, active owned session, profile routing | Structured runtime inspector on the existing connection |
| Future Dots | Separate repository/adapter | Contract fixtures only until cutover is separately authorized |

## Authoritative vocabulary

Project/artifact/capture and current immutable versions: BE07 store and RPCs.
Memory/status/correction acknowledgments: BE08; primary personal harness only,
specialist isolated built-in memory. Mission and verification: BE09. Research,
evidence and domain packages: BE10. Workflows: BE11. Schedules/commitments and
correspondence: BE12. Specialist/media/service scopes: BE13. Repair and data
lifecycle: BE14. Optional decision receipts: BE15/16, off/shadow until qualification.

## Consumer rules

- The live transport session ID and durable snapshot lineage ID can differ.
- Connection/profile/session changes invalidate prior async results and clear caches.
- Disconnection means stale knowledge; it does not cancel accepted work.
- Capability negotiation precedes runtime commands. Missing/version-incompatible
  capability leaves a useful disabled state; no implicit fallback mutation.
- Each command gets one identity and the displayed base revision. Unknown outcome
  retains that command for explicit identical retry. Double submission is blocked.
- A command acceptance or stream end never marks a mission completed. Even an
  execution-completed state requires separate artifact/delivery inspection.
- Replay cursor gaps and page limits lead to a fresh authoritative snapshot.
  No client reconstructs authority from incomplete event history.
- Exact approvals use dedicated backend binding APIs; generic runtime approval is
  disabled when the backend directs the client to `runtime.approval.resolve`.
- No secrets, private model traces, raw environment or personal-memory copies are
  introduced into view state. Current task preferences are not grants.

## Verification and remaining gates

Focused shared-controller tests exercise selection/disconnect and uncertain-command
retry through an injected typed transport. They are not live service or hardware
certification. Existing Python producer contract freshness/replay checks remain the
integrator's authority. FE01 onward will add discoverable consumers and operation
specific receipts; a shared controller alone is not a delivered user journey.
