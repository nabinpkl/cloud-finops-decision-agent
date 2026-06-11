# ADR 0016: AG-UI transport for the agent surface; FastAPI as an AG-UI server

- **Status:** Accepted (implementation pending)
- **Date:** 2026-06-11
- **Related:** [0009](0009-agent-runtime-in-fastapi-openai-agents-sdk.md),
  [0012](0012-agent-runtime-port.md),
  [0013](0013-verified-answer-plan-rendering.md),
  [0014](0014-agent-hardening-threat-register.md)

## Context

v0 ships the agent surface over assistant-ui's assistant-transport protocol with
`assistant-stream` (ADR-0009). The product is evolving into a dual-surface
comparison tool: a deterministic table the human drives manually, and a sidebar
agent that operates the *same* table rather than printing a separate one. That
co-driver model needs a shared, mutable view-state that both the human form and
the agent read and write, with the backend as the single source of truth.

assistant-transport has no first-class shared-state channel. AG-UI (the
Agent-User Interaction protocol) does: it is an event-based wire protocol with
`STATE_SNAPSHOT` and `STATE_DELTA` events alongside `TEXT_MESSAGE_CONTENT`,
`TOOL_CALL_START`, and tool-result events. AWS Bedrock AgentCore Runtime added
AG-UI support in March 2026, so an AG-UI-speaking backend is portable to that
ecosystem later.

The open worry was the frontend. AG-UI is a protocol, not a UI library, so
"adopt AG-UI" did not have to mean "replace assistant-ui." assistant-ui ships an
official AG-UI runtime adapter, `@assistant-ui/react-ag-ui` (example updated
2026-05-15), that wraps an `@ag-ui/client` agent and carries `STATE_SNAPSHOT` /
`STATE_DELTA`, tool calls, and client-side tool execution. CopilotKit is the
AG-UI-native alternative client, but adopting it would mean rebuilding the chat
shell and the `ComparisonTable` Tool component for no protocol gain.

## Decision

1. **The backend becomes an AG-UI server.** `POST /assistant` emits AG-UI events
   instead of the assistant-stream shape. The agent runtime port (ADR-0012)
   stays: adapters still stream neutral `Emitter` verbs, and a single AG-UI
   encoder maps those verbs plus state mutations onto AG-UI events. Adding AG-UI
   touches the encoder, not the adapters.

2. **The frontend keeps assistant-ui via `@assistant-ui/react-ag-ui`.** The chat
   shell and the `ComparisonTable` Tool component are preserved. Only the runtime
   binding changes, from `useAssistantTransportRuntime` to the AG-UI adapter.
   CopilotKit is rejected for v0; revisit only if the adapter proves insufficient.

3. **State is backend-authoritative, streamed as AG-UI state events.** The
   canonical view-state (filters + current result set + selection) lives in the
   FastAPI process. The agent and the manual form both mutate it through the
   backend; the backend broadcasts a single `STATE_SNAPSHOT` after the turn
   completes. (As shipped the transport is **snapshot-only**: it buffers the turn
   and emits one full snapshot, observationally equivalent to deltas for a
   single-turn round-trip. `STATE_DELTA` is reserved for a future incremental
   variant and is not emitted today.) The frontend renders state; it does not own
   it.

4. **The hardening surface migrates with the transport, not around it.** The XML
   trust-zone wrapping, body/history/turn limits, budget enforcement, the
   mandatory input judge (ADR-0015), and verified `AnswerPlan` rendering
   (ADR-0013) are transport-independent and must remain in force after the swap.
   The migration step re-verifies the security and budget test suites, not only
   the happy path. No price, citation, or claim binding moves to the wire layer;
   `normalize.wire` still strips `store_path` below the transport.

5. **The conversation thread is client-owned and fully untrusted.** AG-UI's
   `RunAgentInput` carries the entire conversation in `messages[]` on every turn,
   so the backend rebuilds the thread from client input each request
   (`apply_agui_messages`) and keeps **no server-side conversation store**. This
   is deliberate for a stateless, public, read-only pricing agent. Every
   client-supplied message is distrusted at the boundary:
   - both user and prior-assistant text are XML-escaped and wrapped in untrusted
     trust-zone tags (`<external_user_request>` / `<previous_assistant_message>`);
   - only `user`/`assistant` roles become turns — client `system`/`developer`/
     `tool` messages are dropped, so no client role maps to a trusted zone and no
     client can inject a `<trusted_tool_result>` into history;
   - message count, per-message length, and total history are capped (422), and
     the body-size middleware and token caps bound amplification;
   - view-state is reseeded server-side (`prepare_incoming_state`), so a
     client-supplied `view`/`selection`/`sessionLimitReached` is never trusted;
   - `AnswerPlan` price claims bind to the latest **real** tool result from this
     turn, not to anything in the client-supplied history.

   The accepted consequence: **session identity (cookie + hashed IP) governs
   budget and rate limits, not conversation integrity.** The backend cannot
   detect tampered or replayed history, and forged assistant history is a
   prompt-injection surface that the trust-zone wrapping + input judge mitigate
   but do not eliminate (the prompt is not a security boundary; the deterministic
   citation/validation layer is). This is safe because nothing in the
   client-supplied history is authoritative: it cannot fabricate a price, leak an
   internal path, grant a trusted instruction, or mutate view-state. A
   server-side thread store would only be required if history ever became
   authoritative (per-user state, private data, persistent memory).

## Consequences

**Good.** A shared-state channel exists for the co-driver model without a bespoke
protocol. The frontend investment is preserved. The backend is portable to AG-UI
hosts (AgentCore and others). The runtime port keeps framework swaps independent
of the wire change.

**Cost.** A real transport migration: the assistant-stream encoder and the
frontend runtime binding are replaced, and `@ag-ui/client` plus
`@assistant-ui/react-ag-ui` become frontend dependencies. The hardening tests
must be re-run against the new wire, and any assistant-transport-specific request
shaping is rewritten as AG-UI.

**Risk.** The adapter's coverage of `STATE_DELTA` for our backend-authoritative
pattern must be verified early; if it falls short, the fallback is a thin custom
AG-UI client, not a return to assistant-transport.

## Alternatives considered

- **Stay on assistant-transport, add a side channel for state.** Keeps the
  current wire but invents a non-standard state mechanism, with no ecosystem
  portability. Rejected: AG-UI already standardizes exactly this.
- **Switch the whole frontend to CopilotKit.** AG-UI-native, but discards the
  assistant-ui chat shell and `ComparisonTable` for no protocol benefit the
  adapter doesn't already provide. Rejected for v0.
- **Tool-render only (no shared state yet).** Keep `tool_call -> ComparisonTable`
  and defer the co-driver. Ships fastest but the agent prints beside the form
  instead of operating it, which is the product premise. Rejected.

## Status of the implementation

Not started. First build step in the current TASKS plan (transport spine):
migrate the backend to an AG-UI server, swap the frontend runtime to the AG-UI
adapter, and prove backend-authoritative state end to end with the existing fixed
table still rendering, with the hardening suite green.
