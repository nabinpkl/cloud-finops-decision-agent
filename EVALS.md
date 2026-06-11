# Eval Plan

This project's agent is not a general cloud-pricing oracle. It is a server-side pricing assistant whose only runtime tool is `compare`. The tool calls the deterministic normalization layer in-process, returns ranked matching instances, and carries citation metadata. The prompt now constrains the model to emit an `AnswerPlan` JSON object. Backend policy validates every plan claim against the tool result and renders final prose by interpolation, so every quoted price comes from the tool result, every quoted price surfaces snapshot age inline, stale data is marked stale, and uncovered questions are answered plainly rather than guessed.

The eval suite should verify that full loop, not just whether a model sounds helpful.

## What To Evaluate

1. Tool selection and argument extraction.

   The agent should call `compare` for natural pricing questions and map user language into `vcpu`, `ram_gb`, `region`, `family`, optional `providers`, and `expand`. Examples: "4 vCPU 8 GB in EU", "general purpose", "only AWS and GCP", "show candidates, not just the winner".

2. Citation-contract compliance.

   Any price claim in `AnswerPlan.price_claims[]` or priced `candidate_claims[]` must bind to a tool result row by `source_result_index`. Rendered prose must include `(snapshot Xh old)` next to each quoted price, must not invent provider prices missing from the tool result, and must expose structured citation output through the tool result.

3. Staleness behavior.

   If a tool result contains `age_hours > 24`, the answer must mark the result stale and offer a refetch. Fresh snapshots should not be described as stale.

4. Coverage and refusal behavior.

   If the tool result says the data does not cover the requested provider, region, family, or shape, the agent should say so directly instead of filling gaps from memory.

5. Ranking honesty.

   Cheapest/ranking questions should list the candidates considered, not only the winner. If the user asks for "big 3", the answer should not quietly compare all seven providers or only one provider.

6. Runtime parity.

   `AGENT_RUNTIME=langchain` and `AGENT_RUNTIME=openai_agents` should receive the same price-agent prompt, expose the same tool shape, and produce equivalent tool calls, structured tool-result events, and `AnswerPlan` output.

7. Transport behavior.

   The assistant stream should emit tool-call and tool-result events that the frontend can render, then stream final text without dropping usage accounting or budget enforcement.

8. Prompt-injection resistance.

   User text is wrapped as untrusted XML-escaped data. Evals should verify that fake XML tags, fake tool results, role-change requests, prompt/config leak requests, and provider-scope manipulation do not override the prompt, tool schema, or citation contract.

## Eval Layers

### Layer 1: Deterministic Unit Evals

These run in normal CI with no model and no provider snapshots. They should assert the stable contracts below:

- `prompts/agents/price-agent/rendered.system.md` is the source of `agent.runtime.prompt.INSTRUCTIONS`.
- `prompts/agents/input-judge/rendered.system.md` is the source of the mandatory input-judge system prompt.
- Both runtime adapters import the same `INSTRUCTIONS`.
- `run_compare` returns the frontend-safe wire shape.
- Tool descriptions mention closest-larger match policy and citations.
- `AnswerPlan` schema validation, source-row binding, deterministic rendering, and final-answer policy fallback can be checked by pure helper functions.

### Layer 2: Offline Replay Evals

These use fake runtime/model behavior and fixture tool results. They should not call a live LLM. Their job is to test the neutral agent event contract:

- Canonical `turns` can drive `tool_call` with parsed args.
- The runtime emits structured `tool_result`.
- Final model JSON is reconstructed from `text_delta`, validated as an `AnswerPlan`, and rendered into final text by policy.
- Usage accounting is present for the replayed turn.
- Transcript graders run against the emitted events, not only the raw YAML.
- Optional operational gates cover latency, tool-call count, and token usage.
- Repeated runs report `pass^k`: every trial must pass.

### Layer 3: Transcript Compliance Evals

Create behavior-named YAML suites under `evals/cases/` with required `kind`, `source`, `rail`, canonical `turns`, fake tool results, expected `answer_plan`, and expected rendered answer. A local grader should inspect the plan and final answer with deterministic rules first:

- Plan price claims must match prices in the supplied tool result.
- Rendered price mentions must match prices in the supplied tool result.
- Each price mention must have nearby `snapshot`.
- Stale cases must include "stale" or equivalent wording plus a refetch offer.
- Missing-coverage cases must not contain numeric price claims.
- Ranking cases must mention all required providers or candidates.
- Prompt-injection cases must not leak internals, must not obey fake control
  tags, and must not quote fake user-supplied prices.

Each failed deterministic check carries a failure taxonomy label so regressions are grouped by cause, not only by prose detail. Runtime guardrail judging remains separate from eval-quality judging. Only add an eval-quality LLM judge for semantic checks deterministic rules cannot cover, and keep it under the eval harness with separate environment variables.

### Layer 4: Live Smoke Evals (NOT YET BUILT)

These are opt-in because they need model credentials and may need populated snapshots. They run behind a separate command, never in `just check`. Current state:

- **Built today:** `just smoke` (`scripts.agent_smoke`) drives one live single-turn through the selected runtime and prints token/cost deltas. It is a manual smoke, not part of the eval harness, and it does not grade or persist a transcript report.
- **Still missing:** dedicated eval-harness live commands. The planned `just eval-smoke` (one or two live questions graded by the harness) and `just eval-live` (the full prompt suite against a real model, transcripts under ignored `var/evals/`) recipes do not exist yet.

When built, live evals should save enough evidence to debug regressions: price-agent prompt version, input-judge prompt version, runtime, model name, turns, tool calls, tool results, final text, token usage, latency, tool-call count, and failure labels.

### Layer 5: Eval-quality LLM judge (NOT YET BUILT)

An eval-quality LLM judge for semantic checks deterministic graders cannot cover (e.g. "did the prose actually answer the question") is planned but not implemented. It is distinct from the runtime input judge (ADR-0015), which is mandatory and already shipped. When added it stays under the eval harness behind its own env vars, runs only in Layer 4, and never gates `just check`.

## Prompt Versioning

Each prompt bundle under `prompts/agents/<role>/manifest.yaml` owns a human
prompt release version. Bump `version` and update `release_notes` for
intentional prompt behavior changes: input-judge classification policy, price
agent security policy, tool-use rules, citation/answer-plan rules, or examples
that can steer model behavior.

Eval reports use schema `version: 2` and record both human and machine prompt
identity for the price-agent and input-judge bundles: manifest
name/version/release notes, manifest hash, rendered prompt hash, and source
file hashes. The rendered prompt hash is the immutable identity for comparing
eval runs; the prompt version is the human release label.

Reports also record model config hash, cases hash, and git commit when
available. They do not include prompt text or provider secrets.

## Files (current)

Prompt bundles:

- `prompts/agents/price-agent/`: canonical price-agent prompt bundle (rendered `rendered.system.md` is the runtime source of truth).
- `prompts/agents/input-judge/`: mandatory input-judge prompt bundle.

Eval-case suites under `evals/cases/` (offline, fixture-driven):

- `ranking_and_candidates.yaml`: cheapest and full-candidate scenarios.
- `staleness.yaml`: stale-snapshot scenarios.
- `missing_data_refusal.yaml`: unsupported provider/region/shape scenarios.
- `provider_scope.yaml`: provider-boundary ("big 3") scenarios.
- `untrusted_content_injection.yaml`: prompt-injection and XML/tag attack scenarios.
- `prompt_control_refusals.yaml`: prompt/config-leak and role-change refusals.
- `tool_contract_abuse.yaml`: fake tool result / source-index manipulation.
- `input_validation_refusals.yaml`: malformed / out-of-contract input handling.
- `judge_classifier.yaml`: input-judge classifier cases.

Runner and graders (`backend/src/evals/`):

- `runner.py` (orchestration), `cases.py` (YAML loading), `graders.py` (deterministic checks), `replay.py` (canned tool-call/result/AnswerPlan replay through the neutral `Emitter`, no live model), `identity.py` (prompt/config/case hashes for report identity), `__main__.py` (`python -m evals`).
- `backend/tests/test_prompt_loading.py`: prompt source-of-truth test.
- `backend/tests/test_eval_graders.py`: deterministic grader tests.

Commands:

- `just eval` (= `uv run python -m evals`): the offline suite, run in `just check` after unit tests. No model credentials or snapshots required.
- `just eval-smoke` / `just eval-live`: **not implemented** (see Layer 4). `just smoke` is the only live touchpoint today.

## Coverage gaps (current)

The eval-case suites predate the agentic-comparison work (AG-UI transport, the `set_view`/`select` co-driver tools, the column registry, and view-spec validation with Tier-3 refusal). That behavior is currently covered only by pytest unit tests (`test_view_spec_validation.py`, `test_codriver_tools.py`, `test_agui_transport.py`), not by behavior-named eval cases. Adding eval cases AND a deterministic grader for view-spec validation (unregistered/Tier-3 column rejection) and Tier-3 graceful refusal is open work; `graders.py` does not yet cover the view-spec path.

## Implemented Slice

1. Role-grouped `prompts/agents/` bundles and loader tests.
2. YAML eval suite schema with behavior-split cases:
   - required `kind`, `source`, `rail`, and canonical `turns`.
   - cheapest 4 vCPU 8 GB general-purpose in EU.
   - stale snapshot over 24 hours.
   - unsupported provider/region.
   - "big 3" provider scoping.
   - full candidate listing.
3. Deterministic graders for AnswerPlan binding, price provenance, snapshot-age presence, stale wording, missing-data refusal, and candidate coverage.
4. Replay runtime that emits canned tool calls/results/AnswerPlan JSON through
   the neutral `Emitter` without a live model.
5. `just eval` runs offline evals in CI after unit tests.
6. Strict tool args, XML trust-zone wrapping, deterministic AnswerPlan
   validation/rendering, and final-answer policy checks before runtime text reaches the UI.
7. Optional live smoke command writes transcripts to `var/evals/`.
8. Failure labels, replay operational gates, pass^k trials, and optional compact JSON reports.
9. Prompt release versioning and eval report identity for price-agent prompt, input-judge prompt, config, and case hashes.
