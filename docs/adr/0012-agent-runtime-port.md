# ADR 0012: Agent-runtime port — a framework-neutral seam for swapping the agent loop

- **Status:** Accepted
- **Date:** 2026-06-02
- **Related:** [0009](0009-agent-runtime-in-fastapi-openai-agents-sdk.md),
  [0010](0010-observability-via-otel-jsonl.md),
  [0011](0011-public-endpoint-threat-model.md)

## Context

ADR-0009 put the agent loop in-process on the OpenAI Agents SDK. Since then
the SDK/provider pairing has shown sharp edges around OpenAI-compatible
provider-specific request parameters and structured output enforcement. That is
one symptom of a general fact: the framework choice is not free, and we want the
ability to run the same agent on a different framework to compare them on the
same workload, behind one env var, without a rewrite.

The risk in adding a second framework is entanglement. The valuable, hard-won
parts of this repo are framework-independent: the citation contract
(`normalize` + `normalize/wire.py`), budget enforcement (ADR-0011), session identity,
and the assistant-ui wire shape. If a second framework's idioms leak into those,
each framework swap re-touches the asset. The point of this ADR is to draw the
boundary once so that adding, swapping, or removing a framework touches only an
adapter.

Before this change, the coupling was already small but unnamed: the assistant
transport
imported `agents` types in exactly two spots (constructing/running the agent, and
the `stream_events()` translation loop), and the cross-turn input was already a
neutral `[{role, content}]` list. This ADR names that seam and moves the two
coupled spots behind it.

## Decision

### 1. A one-method port

`agent/runtime/types.py` defines the contract transport speaks, and it imports no
agent framework:

- `Turn(role, content)` — one cross-turn message in (text-only in v0).
- `RunUsage(input_tokens, output_tokens)` — a **mutable** token accumulator the
  caller owns and passes in. The runtime writes into it as the run progresses.
- `Emitter` — a protocol with three neutral verbs: `text_delta(text)`,
  `tool_call(call_id, name, args_text, args)`, `tool_result(call_id, result)`.
- `TurnTokenCapExceeded` — the per-turn cap exception, neutral so transport
  catches the same class no matter which runtime raised it.
- `AgentRuntime` — the port: `async run(turns, emit, usage) -> None`.

Transport hands a runtime the turns and an emitter, the runtime streams parts
back through the emitter and accumulates into `usage`. That is the entire
surface.

### 2. Why `RunUsage` is passed in, not returned

A turn can abort mid-stream (the cap fires, or the model errors). ADR-0011
requires that a partial turn still pays for what it consumed. A returned value is
lost when the call raises; a caller-owned accumulator the runtime mutates is
readable in transport's `finally` regardless of how the run ended. This mirrors
the pattern the Agents SDK `BudgetHooks` already used (counters on `self`),
generalized so every adapter obeys it.

### 3. Ours vs adapter — the boundary rule

```
ours/  (framework-free — the asset; must NOT import agents/langchain)
  agent/runtime/types.py      the port + neutral types
  api/assistant_transport/    HTTP, session, budgets, StateEmitter, the agent.turn span
  api/budgets.py  api/middleware.py
  agent/tools/pricing.py      run_compare = normalize.compare + wire_response  (tool logic)
  normalize/...  normalize/wire.py  the deterministic data + citation layer

adapter/  (framework glue — swappable; imports exactly one framework)
  agent/runtime/openai_agents/   Agent + OpenAIChatCompletionsModel; Runner.run_streamed;
                                 stream_events() -> Emitter; BudgetHooks for the cap
  agent/runtime/openai_agents/  (the OpenAI-agents binding of the above)
  agent/runtime/langchain.py     create_agent; StructuredTool wrapping run_compare;
                                 LangGraph stream -> Emitter; middleware cap enforcement
```

The invariant: **`agent/tools/pricing.py` and everything under `ours/` import no
agent framework.** Each adapter wraps the *same* `run_compare` in its own tool
decorator. The citation translation lives in `wire_response`, below both
frameworks, so neither can weaken it. This is the property ADR-0009's amendment
and ADR-0011 depend on: the agent never sees a path the user cannot verify, and
that guarantee cannot be a framework's responsibility.

### 4. Selection by env

`settings.agent_runtime` (env `AGENT_RUNTIME`, default `langchain`) chooses
the adapter. `agent/runtime/get_runtime()` imports the chosen adapter **lazily
inside the branch**, so a runtime whose dependency is not installed (e.g.
`openai_agents`) never breaks the default path, and flipping the experiment is
one env var. The active runtime is recorded on the `agent.turn` span as
`finops.agent.runtime`, so traces and the smoke can be attributed per framework.

### 5. The cap is policy (ours); the hook is plumbing (adapter)

`TurnTokenCapExceeded` and the "stop at `turn_token_cap`" rule are ours and
neutral. *How* a framework counts tokens and interrupts differs: the Agents SDK
gives a per-LLM-call `on_llm_end` hook; LangChain gives middleware around model
calls. Each adapter enforces the same neutral exception with
its own mechanism. A consequence: token-counting **granularity** differs across
runtimes (per-call vs per-step), so the smoke's per-call deltas will not line up
one-to-one between frameworks. The cumulative per-turn total, which is what the
cap and `record_usage` care about, is the same.

## Consequences

**Good.** Adding a framework is one adapter file plus one `get_runtime` branch.
Transport, budgets, and the citation layer are untouched by a framework swap.
Provider-specific model request shape is adapter-local: it ships inside
whichever adapter needs it and cannot disturb the other.

**Cost.** One more indirection between transport and the SDK, and a small
duplication: each adapter re-implements the stream-event-to-`Emitter` mapping for
its framework. That mapping is exactly the framework-specific work the port is
meant to contain.

**Known wart — observability still imports `agents.tracing`.** `api/observability.py`
bridges the Agents SDK's internal spans into the OTel JSONL (ADR-0010). It lives
in an "ours"-looking file but is a second framework coupling. It is additive and
only fires under `openai_agents`; under `langchain` it produces nothing, so it
is not broken, but it is not neutral either. The neutral `agent.turn` span in
transport is unaffected. Cleaning this (a tracing bridge per adapter; LangChain
traces via LangSmith/LangGraph) is deferred to the LangChain adapter step, not
done here, to keep this change a behavior-preserving refactor.

**Granularity caveat (restated).** Cross-runtime token deltas are not
comparable per-call. Document this wherever the smoke output is read so a
per-call difference is not mistaken for a bug.

## Alternatives considered

- **Keep one framework, patch the SDK.** Subclass provider clients to force
  provider-specific request parameters. This leaves us single-framework and
  unable to compare. The port does not preclude this; it just makes it one
  adapter's internal choice.
- **Process-isolate each framework behind HTTP.** A separate service per runtime.
  Heavier than the problem: ADR-0009 deliberately runs the loop in-process for
  the in-repo data path. The port gives swappability without a network hop.
- **Return usage instead of a passed-in accumulator.** Loses partial usage on
  abort, violating ADR-0011's "a partial turn still pays." Rejected.

## Status of the implementation

The port, the OpenAI-agents adapter, the env router, and the boundary refactor
of the assistant transport shipped behind no behavior change (`just check`
green). A later amendment added the LangChain adapter and made it the default.

## Amendment 2026-06-02: LangChain adapter shipped; default flipped

The LangChain adapter (`agent/runtime/langchain.py`) is implemented: lean
`langchain.agents.create_agent` with the single `compare` tool, the
stream-to-`Emitter` mapping, a `CapMiddleware` enforcing the neutral per-turn
cap, strict provider-native `AnswerPlan` structured output, and OpenRouter
parameter-compatible routing via `provider.require_parameters`.

Two decisions in this ADR are superseded:

- **Default runtime is now `langchain`, not `openai_agents`.** The LangChain
  runtime drives DeepSeek-via-OpenRouter correctly and can request provider
  native strict JSON Schema output for the final `AnswerPlan`. Verified live:
  DeepSeek V4 Flash accepts strict `json_schema` with
  `provider.require_parameters=true`; OpenRouter routes to a compatible provider
  and returns valid JSON in `message.content`. Reasoning remains supported, but
  it is not round-tripped through a custom model subclass.
- **`langchain`/`langchain-openai` are now core dependencies; `openai-agents`
  is an optional extra.** A default runtime must work on a plain `uv sync`, so
  the langchain stack is core. The Agents-SDK -> OTel tracing bridge was the last
  module-load importer of `agents`; it is now extracted to
  `api/observability_agents_bridge.py` and imported lazily by
  `init_observability` only under `agent_runtime == "openai_agents"`. With that,
  `agents` is imported on no default-path module load (verified:
  `import api.main` pulls in no `agents`), so `openai-agents` moves to
  `[project.optional-dependencies]` (`uv sync --extra openai-agents`). It stays
  in the dev group so the local suite exercises both runtimes; the Agents-SDK
  tests are `importorskip`-gated.

The observability wart noted above is fully resolved: the bridge is neither
imported nor registered under the default runtime. The neutral `agent.turn` span
carries `finops.agent.runtime` for both.
