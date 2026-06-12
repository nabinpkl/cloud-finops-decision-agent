# Screenshots

UI states of the `ComparisonTable` agent tool component, rendered with **real**
`compare()` data (the wired tool-result shape the frontend consumes).

| File | State |
|---|---|
| `comparison-table-full.png` | Ranked 7-provider result for 4 vCPU / 8 GB general-purpose in eu-central: Region column (native + canonical), stale-data badge + age pills, `derived` badges on synthesized GCP/Oracle rows, composite sub-rows (per-rate contribution + source), and the equivalence-basis footnote (`dimensions_not_normalized`). |
| `comparison-table-fresh.png` | Same rows with fresh snapshots: no stale badge, recent age pills. |
| `comparison-table-unmet.png` | No/partial match: `unmet_requirements` rendered. |
| `comparison-table-loading.png` | Loading skeleton (tool call in flight). |
| `dashboard-mock.png` / `-dark.png` | **Layout mock**, agent panel **open** (Copilot-style, *not* a modal). Opening the panel **shifts the page left** and docks the agent flush-right, so the deterministic dashboard and the agent stay visible together — no scrim, no dimming. Because both are on screen, the agent is grounded in what you're looking at: a **"Looking at 4 vCPU · 8 GB · general-purpose · eu-central · live"** context strip mirrors the current comparison, and answers reference "your current view". Summoned from an agent icon — the navbar **Ask AI** button or the floating launcher. Cited answers; it does **not** reshape the manual table (no `set_view`). Refetch is operator/agent-side, so it is absent. |
| `dashboard-mock-closed.png` | Same mock, agent panel **closed**: the deterministic dashboard at full width, with the floating agent launcher (sparkle FAB + caption) bottom-right. Shows the on-demand open/close model. |

## `dashboard-mock.html` — design artifact, NOT a runtime answer

`dashboard-mock.html` is a self-contained static mock (no build, no data layer;
open directly in a browser). It exists because the `comparison-table-*` shots
render the table component in isolation, which doesn't convey the dual-surface
product (human-driven table + sidebar co-driver). Tokens mirror
`frontend/app/globals.css`.

Its numbers are **illustrative** and do **not** trace to a snapshot on disk, so
it deliberately sits outside the citation contract — it is a design artifact for
communicating layout, never a price quote. The `dashboard-mock.png` /
`dashboard-mock-dark.png` images are headless Playwright renders of it at 2×.

## How they were generated

These are not live agent-flow captures (that needs a reachable model + a browser
automation bridge). They are headless renders of the real component fed the real
`compare()` fixture, so every pixel is the shipped UI with real data:

1. Produce the wired fixture:
   `cd backend && uv run python -c "import json; from normalize.query.service import compare; from normalize.wire import wire_response; json.dump(wire_response(compare(vcpu=4, ram_gb=8, region='eu-central', family='general-purpose')), open('fixture.json','w'))"`
2. Add a temporary client route under `frontend/app/<name>/page.tsx` that renders
   `ComparisonTableImpl` with that fixture across the states above (mark
   `ComparisonTableImpl` exported while doing so).
3. Headless-screenshot each section with Playwright (chromium), then remove the
   temporary route, the export, and the Playwright dev dependency.

The live agent chat answer and the citation excerpt-on-click hunk are interactive
(backend + model) and are not captured here.
