# ADR 0009: Agent runtime in FastAPI on the OpenAI Agents SDK; web/ is frontend-only; provider is a base-URL knob

- **Status:** Accepted
- **Date:** 2026-05-28
- **Supersedes:** parts of [0008](0008-vertical-slice-api-and-citation-excerpt.md) (the agent-runtime location and the fixed-Anthropic provider line; 0008's slice scope, citation snapshot-ref, and serve-time excerpt decisions stand)
- **Related:** [0005](0005-data-quality-envelope.md), [0008](0008-vertical-slice-api-and-citation-excerpt.md)

## Context

ADR 0008 locked the vertical slice but assumed a shape that, on closer look, fights the codebase we already have:

- the agent loop ran in the Next.js layer (`web/app/api/chat/route.ts`) on the Vercel AI SDK,
- the LLM provider was fixed to Anthropic,
- the `compare` tool reached the normalization layer over HTTP.

We already operate a Python backend: FastAPI wraps the entire `normalize/` layer. Putting the agent loop in a Next.js route handler means a second backend, in a second language, on a second runtime. The `compare` tool would HTTP-hop back to FastAPI to reach `normalize.compare()` that sits in the same repo. Two deploy targets, two places for the agent's behavior to live. None of that buys anything the Python side cannot do.

Separately, fixing the provider to one vendor is a hardcoded config value by the universal no-hardcoded-config rule: the model endpoint changes between environments and experiments and belongs in `.env`.

## Decision

### 1. The agent loop runs server-side in FastAPI on the Python OpenAI Agents SDK

`openai-agents` (Python) hosts the loop. An `Agent` is built with `instructions`, the `compare` tool, and a model (see 3). FastAPI exposes `POST /assistant` implementing assistant-ui's assistant-transport protocol, which the frontend consumes.

`web/` is a **pure frontend**: Next.js + assistant-ui that renders the stream. It holds no agent logic, no tool definitions, no model keys, and no route handler running the loop. The browser talks to one backend.

### 2. Agent tools call the normalization layer in-process, not over HTTP

The `compare` tool's body calls `normalize.compare()` directly (same for `lookup`). No self-hop. AGENTS.md already names the in-process import as a first-class path for the agent; the HTTP endpoints remain for external/non-agent consumers (CLI users, other services), not for the in-repo agent to reach its own data layer.

### 3. The model provider is an OpenAI-compatible base URL, configured in .env

The agent is built against an OpenAI-compatible endpoint, not a hardcoded vendor:

```python
from agents import Agent, AsyncOpenAI, OpenAIChatCompletionsModel

client = AsyncOpenAI(base_url=PROVIDER_BASE_URL, api_key=PROVIDER_API_KEY)
model = OpenAIChatCompletionsModel(model=MODEL_NAME, openai_client=client)
agent = Agent(name="finops", instructions=..., model=model, tools=[compare])
```

`PROVIDER_BASE_URL`, `PROVIDER_API_KEY`, `MODEL_NAME` are `.env` knobs. Pointing them at OpenAI, an Anthropic OpenAI-compat endpoint, OpenRouter, or a local server is a config change, not a code change.

**Chat Completions, not Responses.** The SDK defaults to OpenAI's Responses API. Almost every non-OpenAI compatible endpoint speaks only Chat Completions, so the default 404s against them. `OpenAIChatCompletionsModel` (or `set_default_openai_api("chat_completions")`) pins the right wire shape. This is the load-bearing detail that makes provider-agnosticism actually work.

### 4. The citation and data_quality contracts from 0008 are unchanged

The snapshot-ref citation (no leaked `store_path`), the serve-time lazy excerpt endpoint, and the `data_quality` envelope all stand exactly as 0008 and 0005 specify. This ADR moves where the agent runs and how the model is chosen; it does not touch what a citation looks like or how freshness is reported.

## Consequences

### Positive

- One backend, one language for both the deterministic query layer and the agent that orchestrates it. No HTTP self-hop on the tool path.
- Provider swap is a `.env` edit. No vendor lock in code; satisfies the no-hardcoded-config rule.
- Model keys never reach the browser. `web/` ships nothing secret.
- The slice still proves the chain end-to-end on the first real query, as 0008 intended.

### Negative

- FastAPI now carries two concerns: the deterministic `compare`/`lookup`/`excerpt` API and the agent runtime. If the agent file grows, it gets its own module under the API package rather than piling into `main.py`.
- **Stream bridge is real work, but bounded.** The frontend uses assistant-ui's `useAssistantTransportRuntime` (POSTs to `/assistant`). assistant-ui ships a first-party Python package (`assistant-stream`) and a reference `assistant-transport-backend`, so the emit side has a helper; the work is bridging the OpenAI Agents SDK's `Runner.run_streamed()` events into assistant-stream state updates (the OpenAI SDK has no assistant-ui adapter of its own). This must be built and verified before R9 can render; it is the first real integration risk of the new architecture and R8 calls it out explicitly. Do not assume it works until a tool call round-trips through the rendered UI.

### Neutral

- The JS Agents SDK + `ai-sdk-ui` extension remains the natural path if the loop ever moves to the edge or to a Node deploy. Rejected now only because the Python backend already exists; revisit if the deploy story (v1, ADR-0008-era "Cloud Run / Fly / Vercel") splits the agent off from the data layer.

## Alternatives considered

- **TS Agents SDK in the Next.js route** (0008's implied shape). Rejected: second runtime, second language, HTTP self-hop to reach an in-repo data layer.
- **Fix the provider to Anthropic** (0008's slice scope). Rejected: a base-URL knob gives provider-agnosticism for free and removes a hardcoded config value.
- **Keep a bare Vercel AI SDK `streamText`+tools loop, no Agents SDK.** Rejected: we want the Agents SDK primitives (typed tools, handoffs, sessions, tracing) and its provider-agnostic client, which the bare loop would re-implement by hand.

## Amendment 2026-05-31: trust boundary for the round-tripped state

The original decision left the trust model of the round-tripped `state` field
implicit. Under [ADR-0011](0011-public-endpoint-threat-model.md) the runtime
is a public anonymous endpoint, so the model needs to be made explicit:

- The `state` field on the assistant-transport request is **UI scaffolding,
  not a source of truth**. The backend uses `state.messages` to reconstruct
  SDK input for the current turn and ignores any other client-supplied
  field (usage counters, session ids, budget hints).
- All enforcement state (per-session token totals, the cookie-keyed session
  identity, the hashed-client-id rate-limit windows, the global daily
  counters) lives server-side per ADR-0011's storage rules. The backend
  reads identity from a first-party cookie it set itself, never from the
  request body.
- The backend continues to **write** UI scaffolding into the streamed
  state (assistant turns, tool-call parts, and a `sessionLimitReached`
  flag when the per-session cap trips). None of those writes carry
  counters; they describe what the UI should render, not what the next
  request is allowed to claim.

This amendment does not change the transport contract or the data shapes
ADR-0009 specifies. It records the trust boundary that every reader of
`request.state` should now assume.
