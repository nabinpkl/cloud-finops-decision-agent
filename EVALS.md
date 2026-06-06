# Eval Plan

This project's agent is not a general cloud-pricing oracle. It is a server-side pricing assistant whose only runtime tool is `compare`. The tool calls the deterministic normalization layer in-process, returns ranked matching instances, and carries citation metadata. The prompt then constrains the model's prose: every price must come from the tool result, every quoted price must surface snapshot age inline, stale data must be marked stale, and uncovered questions must be answered plainly rather than guessed.

The eval suite should verify that full loop, not just whether a model sounds helpful.

## What To Evaluate

1. Tool selection and argument extraction.

   The agent should call `compare` for natural pricing questions and map user language into `vcpu`, `ram_gb`, `region`, `family`, optional `providers`, and `expand`. Examples: "4 vCPU 8 GB in EU", "general purpose", "only AWS and GCP", "show candidates, not just the winner".

2. Citation-contract compliance.

   Any price in final prose must be traceable to a tool result row. The answer must include `(snapshot Xh old)` next to each quoted price, must not invent provider prices missing from the tool result, and must expose citation blocks or structured citation output through the tool result.

3. Staleness behavior.

   If a tool result contains `age_hours > 24`, the answer must mark the result stale and offer a refetch. Fresh snapshots should not be described as stale.

4. Coverage and refusal behavior.

   If the tool result says the data does not cover the requested provider, region, family, or shape, the agent should say so directly instead of filling gaps from memory.

5. Ranking honesty.

   Cheapest/ranking questions should list the candidates considered, not only the winner. If the user asks for "big 3", the answer should not quietly compare all seven providers or only one provider.

6. Runtime parity.

   `AGENT_RUNTIME=langchain` and `AGENT_RUNTIME=openai_agents` should receive the same prompt, expose the same tool shape, and produce equivalent tool calls and structured tool-result events.

7. Transport behavior.

   The assistant stream should emit tool-call and tool-result events that the frontend can render, then stream final text without dropping usage accounting or budget enforcement.

8. Prompt-injection resistance.

   User text is wrapped as untrusted XML-escaped data. Evals should verify that fake XML tags, fake tool results, role-change requests, prompt/config leak requests, and provider-scope manipulation do not override the prompt, tool schema, or citation contract.

## Eval Layers

### Layer 1: Deterministic Unit Evals

These run in normal CI with no model and no provider snapshots. They should assert the stable contracts below:

- `prompts/finops_agent.md` is the source of `agent.runtime.prompt.INSTRUCTIONS`.
- Both runtime adapters import the same `INSTRUCTIONS`.
- `run_compare` returns the frontend-safe wire shape.
- Tool descriptions mention closest-larger match policy and citations.
- Stale/fresh citation examples can be checked by pure helper functions once prose validators exist.

### Layer 2: Offline Replay Evals

These use fake runtime/model behavior and fixture tool results. They should not call a live LLM. Their job is to test the neutral agent event contract:

- A user turn can drive `tool_call` with parsed args.
- The runtime emits structured `tool_result`.
- Final text is reconstructed from `text_delta`.
- Usage accounting is present for the replayed turn.
- Transcript graders run against the emitted events, not only the raw YAML.

### Layer 3: Transcript Compliance Evals

Create behavior-named YAML suites under `evals/cases/` with inputs, fake tool results, and expected behavioral checks. A local grader should inspect the final answer with deterministic rules first:

- Price mentions must match prices in the supplied tool result.
- Each price mention must have nearby `snapshot`.
- Stale cases must include "stale" or equivalent wording plus a refetch offer.
- Missing-coverage cases must not contain numeric price claims.
- Ranking cases must mention all required providers or candidates.
- Prompt-injection cases must not leak internals, must not obey fake control
  tags, and must not quote fake user-supplied prices.

Only use an LLM judge for semantic checks that deterministic rules cannot cover, such as whether the answer clearly explains dimensions matched and not normalized.

### Layer 4: Live Smoke Evals

These are opt-in because they need model credentials and may need populated snapshots. They should run behind a separate command, not in `just check`:

- `just eval-smoke`: one or two live questions against the configured runtime.
- `just eval-live`: the v0 prompt suite against a real model, recording transcripts under ignored `var/evals/`.

Live evals should save enough evidence to debug regressions: prompt version, runtime, model name, user input, tool calls, tool results, final text, token usage, and pass/fail reasons.

## Proposed Files

- `prompts/finops_agent.md`: canonical production system prompt.
- `evals/README.md`: how to run evals and read results.
- `evals/cases/ranking_and_candidates.yaml`: cheapest and full-candidate scenarios.
- `evals/cases/staleness.yaml`: stale snapshot scenarios.
- `evals/cases/missing_data_refusal.yaml`: unsupported provider or region scenarios.
- `evals/cases/provider_scope.yaml`: provider boundary scenarios.
- `evals/cases/prompt_injection.yaml`: prompt-injection and XML/tag attack scenarios.
- `backend/src/evals/`: Python eval runner and graders.
- `backend/tests/test_prompt_loading.py`: prompt source-of-truth test.
- `backend/tests/test_eval_graders.py`: deterministic grader tests.
- `just eval`: fast offline evals using fixtures.
- `just eval-smoke`: one live model smoke eval.

## First Implementation Slice

1. Land the root `prompts/` directory and loader test.
2. Add YAML eval suite schema with five initial cases split by behavior:
   - cheapest 4 vCPU 8 GB general-purpose in EU.
   - stale snapshot over 24 hours.
   - unsupported provider/region.
   - "big 3" provider scoping.
   - full candidate listing.
3. Build deterministic graders for price provenance, snapshot-age presence, stale wording, missing-data refusal, and candidate coverage.
4. Add a replay runtime that emits canned tool calls/results/final text through
   the neutral `Emitter` without a live model.
5. Add `just eval` to run offline evals in CI after unit tests.
6. Add strict tool args, XML trust-zone wrapping, and deterministic final-answer
   policy checks before runtime text reaches the UI.
7. Add optional live smoke command that writes transcripts to `var/evals/`.
