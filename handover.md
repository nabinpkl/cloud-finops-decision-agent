# Handover

A short note on where the project stands for the next agent. The durable docs
are authoritative; this points at them and records only what is not written
down elsewhere (current tree state, gotchas). Transient by nature: delete or
overwrite it once the next slice lands.

## Start here

Follow AGENTS.md's session-start instructions before editing. It links worker
rule files by absolute path that are easy to skip after a context reset; do not
skip them. Then read TASKS.md: it is the source of truth for what is done
(D1-D6) and what is next (R7 onward). Do not re-plan from scratch.

## Where things stand

The normalize layer (7-provider parquet index, `compare`/`lookup`, citations,
`data_quality`) and the HTTP surface (FastAPI: `/compare`, `/lookup`,
`/citation/excerpt`, `/health`) are built and tested. Both test lanes pass and
`just check` (ruff + ty + pytest) is green.

R7 (D7) is done, uncommitted (architecture set by ADR-0009): the agent loop runs
server-side in FastAPI on the Python OpenAI Agents SDK, not in the Next.js layer.
Backend: `api/config.py` (central settings), `api/agent.py` (`build_agent()` on
`OpenAIChatCompletionsModel`, provider via OpenAI-compatible base-URL knob),
CORS/port lifted to config, knobs in `.env.example`. Frontend: `web/` scaffolded
from assistant-ui's `with-assistant-transport` example, frontend-only;
`useAssistantTransportRuntime` POSTs to a same-origin `/assistant` that
`next.config.js` proxies to `BACKEND_ORIGIN` (no CORS round-trip). Renders on
:3000; `just web` runs it. `just check` still green (backend untouched).

The immediate next task is R8: the backend `POST /assistant`. The frontend is
already wired, so R8 is backend-only. It uses assistant-ui's first-party Python
`assistant-stream` package (reference: assistant-ui's `python/assistant-transport-backend`)
to bridge the OpenAI Agents SDK's `Runner.run_streamed()` events into the
assistant-transport stream, plus the in-process `compare` tool. That bridge is
the first integration risk; prove a tool call round-trips before R9 (rendering).
The ordering rationale for everything after R8 is in TASKS.md's Position block.

## Git state

Three local commits sit on top of origin/main, unpushed:

```
docs(tasks): add v0 agent UI iteration ledger
chore(tooling): wire just check to ruff/ty/pytest and clear surfaced debt
feat(api): report snapshot freshness from /health
```

Pushing was left to the maintainer. One caveat: the pushed commit titled
"Refactor code structure for improved readability and maintainability" actually
contains the entire API and test layer; its message misleads. It is already on
origin, so do not rewrite it without the maintainer's say-so.

## How to run

- `just api` runs the FastAPI app on localhost:8000 (uvicorn, reload).
- `just check` runs lint + typecheck + mocked tests. Run it before calling work done.
- `just test-e2e` runs the real-file tests; needs a populated `store/`.
- `just compare 4 8 eu-central general-purpose` exercises the query layer from the CLI.
- `just fetch-force <provider>` refetches one provider's catalog, bypassing the 24h freshness rule.

## Gotchas worth knowing before they bite

- Snapshots in `store/` are several days old, so `/compare` and `/health` report
  `data_quality` status `stale`. That is correct behavior, not a bug. Re-fetch
  with `just fetch-force <provider>` for fresh prices.
- A shell may print `VIRTUAL_ENV ... does not match the project environment
  path` on uv commands. It is a stale env var pointing at a different directory;
  uv ignores it and uses `.venv`. Harmless, do not chase it.
- The citation excerpt's `matched_value` is the raw upstream leaf. For GCP and
  Oracle that is a `{currencyCode, units, nanos}` object, not a plain number.
  Interpret it as `units + nanos / 1e9` (as `normalize/verifier.py` does), not
  by calling `float()` on it.
- The AWS region snapshot is ~200 MB. The first `/citation/excerpt` request that
  touches it pays a ~1s parse; an LRU cache makes repeats fast. Known cost,
  recorded in ADR-0008.
- jsonpath filter expressions in this repo use a single `&`, not `&&`. The
  `jsonpath_ng.ext` dialect rejects double-ampersand.
- `store/` is gitignored and holds large raw catalogs. Never commit it.

## Contracts not to break

- Every quoted price carries a citation and surfaces its age in prose. The API
  drops the internal `store_path` and exposes a `snapshot` ref instead; the
  excerpt endpoint resolves that ref lazily. Shape and rationale are in ADR-0008
  and SPEC.md.
- `compare`/`lookup` stay parquet-only and fast. The raw-file read happens only
  on an excerpt request, never on the query path.
