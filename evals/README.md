# Evals

Offline evals live here as behavior-named YAML suites. They do not call a model
or require pricing snapshots; each case carries a canned tool call, canned tool
result, model-emitted `AnswerPlan`, and expected rendered final answer.

`just eval` runs two CI-safe lanes:

- `transcript`: grade the YAML transcript directly.
- `replay`: emit the YAML case through the neutral runtime `Emitter` verbs
  (`tool_call`, `tool_result`, `text_delta`) and grade the reconstructed case.

Suites are split by what they protect:

- `ranking_and_candidates.yaml`: cheapest-result and full-candidate coverage.
- `staleness.yaml`: stale snapshot disclosure and refetch behavior.
- `missing_data_refusal.yaml`: unsupported provider or region refusals.
- `provider_scope.yaml`: user-requested provider boundaries.
- `prompt_control_refusals.yaml`: role override, prompt reveal, and internal
  path refusal behavior.
- `judge_classifier.yaml`: mandatory judge decisions for ambiguous input
  classifier cases and judge-unavailable blocking.
- `untrusted_content_injection.yaml`: XML/tag injection, tool-result poisoning,
  and multi-turn history poisoning.
- `tool_contract_abuse.yaml`: attempts to manipulate structured answer-plan
  bindings or tool-result indexes.
- `input_validation_refusals.yaml`: adversarial but invalid request fields.

Run:

```sh
just eval
uv run python -m evals --mode transcript
uv run python -m evals --mode replay
```

The live model evals described in `EVALS.md` are intentionally separate and will
write transcripts under ignored `var/evals/`.
