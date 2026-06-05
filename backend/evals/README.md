# Evals

Offline evals live here as JSONL cases. They do not call a model or require
pricing snapshots; each case carries a canned tool call, canned tool result, and
final answer transcript. The runner grades whether the transcript obeys the
agent contract.

Run:

```sh
just eval
```

The live model evals described in `EVALS.md` are intentionally separate and will
write transcripts under ignored `var/evals/`.
