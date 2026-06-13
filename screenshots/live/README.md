# Live app captures

Real screenshots of the shipped workspace running against the local stack
(frontend :3000 → backend :8000) with **real snapshot data** (a ~455h-old store,
hence the stale badges). Not the mock — the implemented S1–S10 build.

- `dashboard.png` — manual Compare result: 7-provider ranked cited table, stale
  badges + red age pills, derived badges (Oracle/GCP), region native+canonical,
  equivalence footnote.
- `row-expanded.png` — a derived row expanded via whole-row click, showing its
  composite rate breakdown (per-OCPU / per-GB) with verify links.
- `agent-panel.png` — Ask AI open: page shifts left, panel docks below the
  full-width navbar, context strip "Looking at 4 vCPU · 8 GB · general-purpose ·
  eu-central".

Captured headlessly via the global `playwright` lib driving the live app.
