# Evals

Offline evals live here as behavior-named YAML suites. They do not call a model
or require pricing snapshots; each case carries a canned tool call, canned tool
result, and final answer transcript. The runner grades whether the transcript
obeys the agent contract.

Suites are split by what they protect:

- `ranking_and_candidates.yaml`: cheapest-result and full-candidate coverage.
- `staleness.yaml`: stale snapshot disclosure and refetch behavior.
- `missing_data_refusal.yaml`: unsupported provider or region refusals.
- `provider_scope.yaml`: user-requested provider boundaries.

Run:

```sh
just eval
```

The live model evals described in `EVALS.md` are intentionally separate and will
write transcripts under ignored `var/evals/`.
