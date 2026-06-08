# ADR 0014: Agent hardening threat register

- **Status:** Accepted
- **Date:** 2026-06-06
- **Related:** [0011](0011-public-endpoint-threat-model.md),
  [0012](0012-agent-runtime-port.md),
  [0013](0013-verified-answer-plan-rendering.md)

## Context

This repo exposes an unauthenticated pricing assistant. The assistant is useful
only if a future reviewer can distinguish "missing hardening" from "hardening not
needed for this threat model." In June 2026, current external guidance converges
on the same themes:

- OWASP LLM Top 10 for 2025 lists prompt injection, sensitive information
  disclosure, supply chain, data/model poisoning, improper output handling,
  excessive agency, system prompt leakage, vector/embedding weaknesses,
  misinformation, and unbounded consumption as the relevant LLM-app risk set:
  https://genai.owasp.org/llm-top-10/
- OWASP Agentic Top 10 for 2026 adds agent-specific risk categories: goal
  hijack, tool misuse, identity/privilege abuse, agentic supply chain,
  unexpected code execution, memory/context poisoning, insecure inter-agent
  communication, cascading failures, human-agent trust exploitation, and rogue
  agents:
  https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- OpenAI's agent safety guidance recommends combining controls: keep untrusted
  data from directly driving behavior, extract structured fields, validate tool
  arguments, use guardrails/tool confirmations, and run trace graders/evals:
  https://developers.openai.com/api/docs/guides/agent-builder-safety
- OpenAI's deep-research safety guidance calls out web, vector store, and remote
  MCP data as prompt-injection and exfiltration surfaces, and recommends trusted
  connectors, logging/review, schema validation, and staged workflows when
  private data and public search mix:
  https://developers.openai.com/api/docs/guides/deep-research
- NIST AI 600-1 treats prompt injection, indirect prompt injection, data
  poisoning, and attack-surface expansion as cybersecurity risks for GAI systems:
  https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
- The joint NSA/Five Eyes guidance recommends incremental deployment,
  continuously assessing evolving threat models, strong governance, explicit
  accountability, rigorous monitoring, and human oversight for agentic AI:
  https://www.nsa.gov/Press-Room/Press-Releases-Statements/Press-Release-View/Article/4475134/nsa-joins-the-asds-acsc-and-others-to-release-guidance-on-agentic-artificial-in/

This ADR records how those risks map to this project.

## Decision

The v0 assistant stays a narrow, read-only pricing agent. We harden it with
deterministic controls at trust boundaries, not by asking the model to "be safe."
Any future feature that adds write tools, web browsing, remote MCP, long-term
memory, code execution, or inter-agent communication must update this ADR or
supersede it before shipping.

## Implemented controls

| Risk | Current control | Files |
| --- | --- | --- |
| Goal hijack / prompt injection | User and prior assistant text are XML-escaped into untrusted trust-zone tags. Prompt says tool output and user text are data, not authority. Final model JSON must pass deterministic policy before UI emission. | `agent/security/untrusted.py`, `prompts/system/manifest.yaml`, `prompts/system/parts/*.md`, `prompts/rendered/finops-agent.system.md`, `api/assistant_transport/policy_emitter.py` |
| Tool misuse / excessive agency | The model has one read-only pricing tool. The tool body calls `normalize.compare` in-process and cannot write files, browse, send network requests, execute shell/code, or call arbitrary URLs. | `agent/tools/pricing.py`, `agent/runtime/langchain.py`, `agent/runtime/openai_agents/tools.py` |
| Tool argument smuggling | `CompareToolArgs` is a frozen Pydantic schema with provider allowlist, region/path sanitization, numeric bounds, and `extra="forbid"`. | `agent/tools/pricing.py` |
| Sensitive information disclosure | Public tool results use `normalize.wire` to remove `store_path` and expose logical snapshot refs. Final-answer policy blocks local paths, secrets, tracebacks, `.env`, system/developer tags, and internal prompt leakage. | `normalize/wire.py`, `agent/policy/final_answer.py`, `agent/policy/answer_plan_validation.py` |
| Improper output handling / misinformation | The model emits `AnswerPlan` JSON. Backend validation binds every price, candidate, snapshot age, citation, and unmet requirement to the latest tool result, then renders prose by interpolation. Free-form model prose is not sent to users. | `agent/policy/answer_plan_*.py`, `api/assistant_transport/policy_emitter.py` |
| Unbounded consumption | `/assistant` has body, command, message-part, text, and history caps. Runtime has per-turn token cap, per-session cap, per-client minute/hour caps, and global daily cap. | `api/assistant_transport/body_limit.py`, `api/assistant_transport/state.py`, `api/budget/*`, `app_config/__init__.py` |
| Accountability / monitoring | Every turn has an OTel span with runtime, history length, budget state, and policy-block attributes. OTel JSONL export is always enabled; content capture remains off by default. | `api/assistant_transport/turn.py`, `api/observability/*`, `app_config/__init__.py` |
| Evaluation / red-team coverage | Offline evals cover tool-call args, price provenance, snapshot age, stale data, missing-data refusal, provider scope, XML/tag injection, prompt/config leak attempts, fake prices, and invalid providers. | `evals/cases/*.yaml`, `backend/src/evals/*`, `backend/tests/test_answer_plan.py`, `backend/tests/test_agent_security.py` |
| Prompt source integrity | Prompt source files are split for review, but runtime reads one rendered prompt. Manifest coverage, SHA markers, render freshness, source ordering, and orphan files are checked in tests. | `prompts/system/manifest.yaml`, `prompts/rendered/finops-agent.system.md`, `agent/runtime/prompt_assembly.py`, `backend/tests/test_prompt_loading.py` |
| Supply chain drift | Runtime/tool logic is framework-neutral where possible. OpenAI Agents SDK is optional, LangChain is isolated to its adapter, and dependency exceptions are documented. | `agent/runtime/*`, `docs/dependency-exceptions.md`, ADR-0012 |

## Deferred or not applicable risks

| Risk / recommended hardening | Current status | Why it is not implemented now | Trigger to implement |
| --- | --- | --- | --- |
| Human approval for high-impact tool calls | Not implemented | The only model tool is read-only comparison. There are no write, delete, send, purchase, refund, deploy, shell, browser, or external API action tools for a human to approve. | Add approvals before any side-effecting tool or any tool that can contact an arbitrary external destination. |
| Remote MCP server approvals and connector allowlists | Not implemented | The assistant has no MCP, web search, vector store, file search, or third-party connector. Tool definitions are static local Python, not remote metadata controlled by another party. | Required before adding MCP, retrieval, web search, browser automation, or connector-supplied tool schemas. |
| Web/link screening and URL exfiltration filters | Not implemented | The model cannot browse or call URLs. `source_url` is citation metadata only; it is not a tool-call destination. | Required before giving the model any web/search/browser tool, especially if private context is present in the same turn. |
| Long-term memory hardening | Not implemented | The agent has no agent-writable long-term memory. Client-supplied cross-turn state is bounded, XML-escaped, and treated as untrusted prior conversation text. | Required before adding persistent memory, user profiles, vector memory, or self-written summaries. Memory writes must be provenance-tagged and policy-reviewed. |
| Inter-agent authentication / protocol security | Not applicable | There is no multi-agent topology and no agent-to-agent protocol. | Required before adding MCP-to-MCP, A2A, swarm, delegated worker, or peer-agent communication. |
| Code execution sandboxing | Not implemented | The model has no code interpreter, shell, Python execution, file-write tool, or local command tool. | Required before adding code execution. Use a locked sandbox with no secrets, no store write access, bounded CPU/time, and explicit approval for host effects. |
| Per-user authentication and authorization | Deferred | v0 is intentionally an unauthenticated public bench. Current controls protect cost and local internals but do not identify users. | Required for multi-user state, private data, paid plans, admin actions, personalized history, or customer-specific pricing. |
| Private-data staging between web and internal tools | Not applicable | The assistant has no private user data source and no public-web tool. The pricing snapshots are public provider catalogs. | Required if private user data and public retrieval/web tools are ever available in the same workflow. |
| Model output classifier / separate monitor model | Deferred | Current deterministic `AnswerPlan` validation is stricter for pricing claims than a second model would be. A monitor model would add cost and non-determinism without new authority in v0. | Revisit only for semantic checks that deterministic policy cannot express, such as richer equivalence explanations or unsafe-but-schema-valid tool plans. |
| Runtime anomaly detection beyond budget/policy spans | Deferred | OTel traces and evals exist, but there is no production alerting loop yet. For a local open bench, this is acceptable. | Required for hosted production, sustained public traffic, or any side-effecting/autonomous behavior. |
| Tamper-resistant snapshot integrity | Deferred and already scoped to v1 | v0 stores hashes/report artifacts inside the same local store. That catches accidental drift but is not notarization. The agent does not get write tools to mutate snapshots. | Required before claiming external integrity or using snapshots as audit evidence beyond this local bench. |
| Rogue-agent containment | Not applicable | The runtime is per-request and does not self-initiate tasks. There are no background autonomous loops or agent identities that can act without a user request. | Required before scheduled tasks, autonomous monitoring, self-improvement loops, or persistent worker agents. |

## Residual risks that still apply

1. **Prompt injection is not solved.** XML tags and prompt wording are not a
   security boundary. The real boundary is strict tools plus deterministic
   `AnswerPlan` validation. If a future change lets untrusted text influence a
   side-effecting tool before policy validation, this ADR is violated.
2. **Unauthenticated traffic can still create operational load.** Budget and
   rate controls bound model cost and request volume, but a public deployment
   still needs ordinary edge controls, logs, and abuse response.
3. **Structured rendering trades expressiveness for verifiability.** ADR-0013
   accepts this for v0. Richer prose is allowed only if claims remain structured
   and validated before rendering.
4. **Dependency compromise remains a normal software supply-chain risk.** The
   agent architecture cannot remove that risk; it only localizes framework glue
   behind adapters and keeps tool authority narrow.

## Mitigation plan landed 2026-06-06

The residual risks above remain real, but v0 now has concrete follow-up controls:

| Residual risk | Mitigation landed | Remaining trigger |
| --- | --- | --- |
| Prompt injection is not solved | Added adversarial eval coverage for poisoned tool-result metadata, user attempts to alter `source_result_index`, and multi-turn injection through prior state. Added negative unit tests for fabricated citations and missing source indexes. | Add a separate intent/plan monitor only if a future semantic risk cannot be expressed as deterministic `AnswerPlan` validation. |
| Unauthenticated traffic can create load | Added the public deployment runbook to `SECURITY.md`, including edge rate-limit shape, cap-lowering steps, budget salt rotation, and `/assistant` disable guidance. | Before a hosted launch, put CDN/reverse-proxy throttling in front of FastAPI and monitor traces/budget DB continuously. |
| Structured rendering trades prose quality | Split lookup rendering from ranking rendering while keeping prose interpolation from verified claims. | Add richer templates only by extending structured, validated plan fields. Do not return to model-authored final prose. |
| Dependency compromise remains supply-chain risk | Added dependency review guidance to `CONTRIBUTING.md`, `just audit`, and `backend dep-tree` helpers. | Before public release or dependency-heavy PRs, run audit commands and document exceptions in `docs/dependency-exceptions.md`. |

## Review rule

When a future reviewer flags an unimplemented hardening control, first classify
it into one of these buckets:

1. Already implemented by a deterministic control listed above.
2. Not applicable because the relevant capability is absent.
3. Deferred with a named trigger.
4. Residual risk that applies now and needs implementation.

Only bucket 4 should become an immediate security fix. Buckets 2 and 3 should be
left documented, not repeatedly rediscovered.
