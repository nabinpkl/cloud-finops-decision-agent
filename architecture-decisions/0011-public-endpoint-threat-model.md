# ADR 0011: Public-endpoint threat model and budget enforcement seams

- **Status:** Accepted
- **Date:** 2026-05-31
- **Related:** [0009](0009-agent-runtime-in-fastapi-openai-agents-sdk.md),
  [0010](0010-observability-via-otel-jsonl.md)

## Context

The agent endpoint is a public, anonymous, client-facing surface. No login,
no API key, no identity. Every request costs real money at a model provider.
ADR-0010 shipped the visibility layer (OTel traces with token usage and a
best-effort cost view); this ADR specifies what the runtime defends against
and where the defenses sit, so that ADR-0010 plus an enforcement change can
ship a usable public deployment without a separate "production hardening"
sweep later.

The frontend, per ADR-0009, round-trips conversation state on every request.
Re-reading that decision under a public trust model: the round-tripped state
is **untrusted input**. It is fine for UI rendering, useless for enforcement.
Any counter, identifier, or budget field a client ships back can be set to
zero by the next request. This ADR makes that explicit and pushes
enforcement state to the server.

## Decision

### 1. Threats in scope

Six wallet-drain shapes, ordered by what they exploit, not by severity:

1. **Volume flood** (distributed). Many sources, each sends one or a few
   requests. Defeated only at the edge.
2. **Single-client loop**. One source, many requests, fast. The crawler /
   tab-in-a-while pattern.
3. **Long-conversation drain**. One source, looks legitimate, racks up
   cumulative tokens over hours of in-thread back-and-forth.
4. **Crafted-prompt output drain**. One request whose input is engineered to
   maximize output tokens.
5. **Tool-amplification drain**. Input that forces an expensive tool path,
   then a context-bloated subsequent model call. The agent loop is the
   amplifier.
6. **Runaway agent loop**. Not malicious input: the agent decides it needs N
   more tool calls to answer and loops past any reasonable bound.

Out of scope for this ADR: prompt injection (a content-trust problem, not a
budget one), scraping for content reuse (an IP problem, not a budget one),
log poisoning (an operations problem). They warrant their own ADRs when the
controls cost more than a paragraph.

### 2. Tokens are the primitive; USD is a view

All counters, all caps, all storage are in tokens. USD is rendered from
tokens at the edge of the system (logs, dashboards, the cost attribute on
spans). Three reasons:

- Tokens are exact; USD drifts as the price table ages.
- Provider billing is the truth source for USD anyway, so the runtime's
  USD number is informational by construction.
- Caps in tokens compose cleanly across providers; caps in USD would have to
  re-resolve every time the price table moves.

`finops.cost_usd` and `finops.cost.estimate=true` (already shipped in
ADR-0010) remain the surfaced view. Enforcement reads tokens.

### 3. Identity primitives

Two pseudonymous keys, both server-derived. Neither requires the user to
identify themselves.

**Session id.** A first-party cookie `finops_session_id` set by the backend
on first request, opaque random value, expires after N minutes idle. Keys
the per-conversation history (via `agents.Session` when adopted, or via the
backend's own usage row in v0) and the per-conversation token cap. Survives
page reload and Wi-Fi changes. Bypassable by clearing cookies, which is
intentional: "start a new conversation" maps to "clear the cookie."

**Hashed client identifier.** `HMAC-SHA256(daily_salt, client_ip)`. The salt
rotates at UTC midnight. The raw IP is never persisted; only the digest
keys per-client rate limits. Properties:

- Same client maps to the same key for as long as the salt is current,
  enough to enforce per-IP/min and per-IP/day limits.
- After salt rotation, yesterday's digests cannot be correlated to today's.
  Logs, traces, and the budget store carry no IPs.
- Bypassable by IP rotation (VPN, mobile-data toggle). Acceptable: the
  global cap is the real wallet protection; the per-client limit raises the
  bar for casual loop abusers, not for determined attackers.

The salt is a process secret (`BUDGET_IP_HASH_SALT_SECRET` in `.env`),
combined with the UTC date to produce the daily-rotating key. Loss of the
secret does not invalidate any user data: nothing decryptable is stored.

A future move behind Cloudflare or a similar edge will let Private Access
Tokens (RFC 9577) replace the hashed-IP layer with a cryptographic
attestation that the client is a real device. Out of scope for v0;
hashed-IP is the bridge.

### 4. Enforcement seams (in request-flow order)

Five seams, each with a different blast radius. Controls listed at the
narrowest seam that can enforce them.

```
Internet
  │
  ▼
[1] Edge (CDN / reverse proxy). Not in v0. Future home for L3/L4 floods,
    bot fingerprinting, and Private Access Tokens.
  │
  ▼
[2] FastAPI middleware. Sees the hashed client id, the cookie session id,
    the URL, the body size. Cheapest seam; refuses before any agent work.
     - Global daily token cap (refuse with 503 past the cap).
     - Global daily request cap (cheap secondary).
     - Per-hashed-id request rate (N/min, M/hour).
  │
  ▼
[3] /assistant handler entry. Has parsed request, has session id (set the
    cookie if absent), can read the per-session usage row.
     - Per-session cumulative token cap.
     - Refuse-and-signal: append a terminal assistant message
       ("This conversation reached its token limit. Start a new one to
       continue.") and set a state flag the frontend can render as a
       sticky banner with a "Start new conversation" button.
  │
  ▼
[4] Pre-`Runner.run_streamed`. Has the input list, knows turn index.
     - `max_turns` on the SDK call (belt-and-braces vs runaway loops).
  │
  ▼
[5] `RunHooks` inside the agent loop.
     - Per-turn token cap (sum of input + output across the turn's model
       calls). On trip: stop the loop, surface a partial-answer warning.
     - Tool result size cap (truncate the model-bound payload, record
       `finops.tool.result_truncated=true`).
  │
  ▼
[6] Post-run. Persist updated session usage; increment global counters.
```

The global daily cap at [2] is the credit-card gate. Everything below it is
gradient: catches the abuser who hasn't yet triggered the global gate, or
the legitimate user who tripped a per-conversation limit.

### 5. Storage

A single SQLite file at `var/budgets.db`, three tables:

- `global_daily(utc_date PK, tokens_input, tokens_output, requests)` — one
  row per UTC day. Read and written on every request.
- `client_window(hashed_id PK, window_start, requests, tokens_input,
  tokens_output)` — sliding window per hashed client id. Rows age out by
  TTL; salt rotation makes any stale row unreferenceable next day anyway.
- `session(session_id PK, created_at, last_seen, tokens_input,
  tokens_output)` — per cookie session, server-authoritative.

SQLite suits the access pattern: small rows, no joins, single host. The
seam for swapping to Redis or a managed store later is one module
(`api/budgets.py`). All persistence is enforcement-only: no PII, no
message text, no IP.

### 6. The round-tripped state is UI-only

Per ADR-0009 the client ships `state.messages` on every request. Under this
ADR's trust model the backend:

- **Reads** `state.messages` only to reconstruct SDK input for the current
  turn (UI rendering of prior turns is the frontend's concern).
- **Ignores** any usage, budget, session-id, or counter field the client
  attempts to inject. The server reads identity from the cookie and tokens
  from its own store.
- **Writes back** UI scaffolding (the assistant turn, tool-call parts, and a
  `sessionLimitReached: true` flag when [3] trips) but no counters.

This is not an ADR-0009 reversal; the state round-trip remains the
transport contract. It is an explicit trust-boundary note that informs
every reader of `request.state` from now on.

## Consequences

- The runtime gains a single new storage dependency (SQLite at
  `var/budgets.db`). Deploy story: a writable volume. Backup: optional, the
  data is recreated naturally on the next day.
- Logs, traces, and budget storage contain zero raw IPs. A reasonable
  privacy claim to make in any future user-facing doc.
- The per-client limit is bypassable by IP rotation. This is documented and
  accepted: the global cap is the real protection; the per-client limit
  catches casual abuse and reduces the rate at which the global gate would
  be triggered.
- The frontend gains a non-trivial new responsibility: render the
  `sessionLimitReached` flag as a sticky banner and a "Start new
  conversation" button. Documented in the implementation plan.
- The global daily cap is a hard cliff. A real public deployment will want
  staged warnings (an alert at 50%, a soft-degraded mode at 80%, refusal at
  100%). Out of scope for v0; refusal at 100% is the floor.

## Alternatives considered

- **Trust the round-tripped state for the per-session counter.** Cheapest
  to build, but in a public trust model the counter is whatever the client
  says it is. Rejected: silently undefeats the gate.
- **No identity at all; rely solely on the global cap.** Works for the
  wallet but means one loop-abusing client can exhaust the day's budget for
  every other user. Rejected: single-actor denial-of-service on every
  legitimate visitor.
- **Raw IPs as the per-client key.** Operationally simpler; a privacy
  regression that bakes itself into logs and storage. Rejected in favor of
  the salted hash, which costs one HMAC per request.
- **Private Access Tokens or hCaptcha now.** Stronger than hashed IP and
  the right destination. Operationally too heavy for v0 (issuer trust
  lists, fallback browser paths). Bridge with hashed IP; revisit when an
  edge tier is in front of FastAPI.
- **USD as the primitive.** Lets caps be expressed in money the operator
  cares about, at the cost of every cap moving when the price table moves.
  Rejected: tokens are exact, USD is the view, and the operator-facing
  "$5/day" knob is still expressible (translated to tokens at config-load
  time using the current price table).
