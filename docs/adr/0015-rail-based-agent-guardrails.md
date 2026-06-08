# ADR-0015: Rail-Based Agent Guardrails

## Status

Accepted.

## Context

This project is a public, unauthenticated pricing agent. Prompt injection is not
solved by a single prompt, a single classifier, or a framework-level guardrail.
The current implementation already has deterministic safety controls:

- strict pricing tool arguments,
- provider and region allowlists in the normalize layer,
- XML escaping for untrusted user, history, and tool-result text,
- structured `AnswerPlan` output,
- deterministic final-answer validation,
- budget and rate enforcement.

NVIDIA NeMo Guardrails and Guardrails AI provide useful rail terminology and
validator patterns, but adding either as a runtime dependency in v0 would add a
new policy language, another dependency surface, and extra behavior indirection
without replacing the deterministic checks this agent needs for citations.

## Decision

Use an in-house rail taxonomy inspired by NeMo Guardrails:

- **Input rail**: classify user intent before the main model runs.
- **Execution rail**: enforce strict tool schemas, provider allowlists, and no
  user-controlled internal paths.
- **Retrieval/tool-result rail**: treat provider catalog strings, citation
  excerpts, prior assistant messages, and external tags as untrusted data.
- **Output rail**: validate structured `AnswerPlan` claims and final rendered
  prose before sending text to the browser.
- **Eval rail**: map YAML eval cases to the rail they protect.

The judge model is mandatory for every user-triggered assistant turn. It is a
small classifier, not a second agent. It never sees the system prompt, rendered
prompt, secrets, local store paths, or raw tool-result internals. If the judge
is unavailable, times out, returns invalid JSON, returns invalid schema, or
returns anything other than `allow` with reason `safe`, the main model is not
called and the user receives a safe refusal.

The judge prompt is a first-class prompt bundle under
`prompts/agents/input-judge/`. Runtime reads its rendered artifact, tests enforce
manifest/render freshness, and eval reports/traces carry its rendered hash and
version alongside the main price-agent prompt.

Deterministic rails remain authoritative. The judge cannot allow a request or
answer that deterministic checks blocked, and it cannot approve prices,
citations, tool arguments, or claim binding.

We deliberately do not add NeMo Guardrails or Guardrails AI as runtime
dependencies in v0. Revisit only if the project needs broad policy moderation
across several agent types or reusable external validator catalogs.

## Consequences

- Judge outage becomes a user-visible safe refusal, not a silent bypass.
- Assistant turns spend judge-model tokens before the main model can run.
- False positives are possible; evals must track benign pricing requests that
  contain words such as "rules" or "instructions."
- Deterministic endpoints such as `/compare`, `/lookup`, `/health`, and
  `/citation/excerpt` remain model-free.
